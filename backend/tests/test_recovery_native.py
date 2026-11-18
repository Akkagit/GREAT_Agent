"""Exercise actual OS process identity with a disposable Python sleeper."""
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from great_agent.db.models import ExecutionApprovalModel, ExecutionPreviewModel, utc_now
from great_agent.db.recovery_repository import RecoveryRepository
from great_agent.db.repositories import TaskRepository
from great_agent.runtime.process_identity import capture_identity
from great_agent.runtime.recovery_service import RecoveryService
from test_worker import repository_context  # noqa: F401


@pytest.mark.parametrize('request_stop', [False, True])
async def test_native_orphan_is_observed_then_released_without_reexecution(repository_context, request_stop):
    sessions, _ = repository_context
    repository = TaskRepository(sessions)
    owner = str(uuid4())
    claim = repository.claim_next('old-worker', owner_session_id=owner)
    executable = Path(sys.base_prefix) / ('python.exe' if os.name == 'nt' else 'bin/python3')
    command = (str(executable), '-c', 'import time; time.sleep(60)', '--mock-attempt', str(claim.attempt_id))
    repository.prepare_spawn(claim.task_id, claim.attempt_id, command, owner_session_id=owner)
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        repository.record_pid(claim.task_id, claim.attempt_id, process.pid, owner_session_id=owner, identity=capture_identity(process.pid))
        with sessions.begin() as session:
            preview_id = str(uuid4())
            session.add(ExecutionPreviewModel(id=preview_id,experiment_id=str(claim.experiment_id),revision_id=str(claim.revision_id),
                payload_json=json.dumps({'jobs':[{'command':command}]}),confirmed_at=utc_now()))
            session.flush()
            session.add(ExecutionApprovalModel(task_id=str(claim.task_id),attempt_number=1,preview_id=preview_id,job_index=0))
        recovery = RecoveryRepository(sessions)
        observer = RecoveryService(recovery, owner_session_id=str(uuid4()))
        await observer.run_once()
        assert repository.get_task(claim.task_id).status == 'running'
        assert recovery.get(claim.task_id)['recovery_state'] == 'observing'
        if request_stop:
            recovery.request_action(claim.task_id,claim.attempt_id,uuid4(),'stop')
        else:
            process.terminate()
            process.wait(timeout=5)
        await observer.run_once()
        assert repository.get_task(claim.task_id).status == ('cancelled' if request_stop else 'interrupted')
        process.wait(timeout=5)
        assert process.poll() is not None
        assert len(repository.list_attempts(claim.task_id)) == 1
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
