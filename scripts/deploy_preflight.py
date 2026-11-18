"""Read-only invited-trial checks; never start services, migrate, or run GREAT.

Default: configuration and filesystem only. --probe: loopback health endpoints
only, with environment proxies and redirects disabled. Public acceptance is a
separate operator step. --report creates a NEW JSON file and never overwrites.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if value and ((tag == 'script' and key == 'src') or
                          (tag == 'link' and key == 'href')):
                self.paths.append(value)


def frontend_ready(dist: Path) -> bool:
    try:
        parser = Assets()
        parser.feed((dist / 'index.html').read_text(encoding='utf-8'))
        if not parser.paths:
            return False
        for value in parser.paths:
            url = urlsplit(value)
            if url.scheme or url.netloc:
                return False
            asset = (dist / unquote(url.path).lstrip('/')).resolve()
            if not asset.is_relative_to(dist.resolve()) or not asset.is_file() or not asset.stat().st_size:
                return False
        return True
    except (OSError, ValueError, UnicodeError):
        return False


def collect_checks(config_path: Path, *, probe: bool = False,
                   environ: Mapping[str, str] | None = None) -> list[dict]:
    checks = []

    def add(code, ok, passed, failed):
        checks.append({'code': code, 'status': 'pass' if ok else 'fail',
                       'message': passed if ok else failed})

    def warn(code, message):
        checks.append({'code': code, 'status': 'warn', 'message': message})

    try:
        from great_agent.config import AppSettings
        env = os.environ if environ is None else environ
        settings = AppSettings.load(Path(config_path), env)
    except Exception:
        # Validation exceptions may embed configuration values or credentials.
        add('configuration', False, '', 'Configuration unavailable or invalid; inspect it locally. Values are omitted.')
        return checks
    add('configuration', True, 'Application configuration validated.', '')
    root = settings.project_root
    add('python', (3, 11) <= sys.version_info[:2] < (3, 13),
        'Supported Python runtime.', 'Python 3.11 or 3.12 is required.')
    expected = (root / 'data/trial/great_agent.db', root / 'runs/trial',
                root / 'data/trial/knowledge')
    actual = (settings.database_path, settings.run_root, settings.knowledge.root)
    isolated = settings.host == '127.0.0.1' and settings.port == 8766 and actual == expected
    add('trial_isolation', isolated, 'Independent trial port and storage paths confirmed.',
        'Trial requires port 8766 and separate data/trial, runs/trial and knowledge paths; do not use production resources.')
    add('single_solver', settings.max_concurrency == 1 and settings.pppflt.default_concurrency == 1,
        'Trial solver concurrency is 1.', 'Set both trial concurrency limits to 1 before rollout.')
    for code, path, is_directory in (
        ('solver', settings.pppflt.executable, False),
        ('xml_template', settings.pppflt.template_path, False),
        ('observations', settings.pppflt.observation_root, True),
        ('products', settings.pppflt.product_root, True),
    ):
        present = path.is_dir() if is_directory else path.is_file() and path.stat().st_size > 0
        add(code, present, 'Configured resource exists.', 'Configured resource is missing or empty; check its local path.')
    add('frontend', frontend_ready(root / 'frontend/dist'),
        'Built page and its local assets exist.', 'Build is missing, references external assets, or has missing assets.')
    if settings.knowledge.embedding_enabled:
        resources = (settings.knowledge.embedding_model_path, settings.knowledge.embedding_tokenizer_path)
        add('embedding_resources', all(path is not None and path.is_file() for path in resources),
            'Configured local embedding resources exist.', 'Provision both configured embedding resources locally.')
    if settings.knowledge.docling_artifacts is not None:
        add('docling_resources', settings.knowledge.docling_artifacts.is_dir(),
            'Configured Docling artifacts directory exists.', 'Configured Docling artifacts directory is missing.')
    if not settings.model.api_key.get_secret_value():
        warn('model_key', 'No model key in this process environment; configure the actual service account separately.')
    else:
        checks.append({'code': 'model_key', 'status': 'pass',
                       'message': 'A model key is present in this process; service-account inheritance is not verified.'})
    warn('scope', 'Static checks do not verify GNSS file completeness, solver compatibility, model connectivity, or service supervision.')

    if probe and isolated:
        opener = build_opener(ProxyHandler({}), NoRedirect())
        base = f'http://127.0.0.1:{settings.port}/api/v1'
        for code, endpoint in (('api_health', '/health'), ('worker_readiness', '/readiness'),
                               ('knowledge_executor', '/diagnostics')):
            try:
                with opener.open(base + endpoint, timeout=5) as response:
                    payload = response.read(65537)
                if len(payload) > 65536:
                    raise ValueError('oversized response')
                body = json.loads(payload)
                if code == 'api_health':
                    ok = body.get('status') == 'ok'
                elif code == 'worker_readiness':
                    ok = body.get('ready') is True
                else:
                    knowledge = body.get('knowledge', {})
                    ok = not settings.knowledge.enabled or (
                        knowledge.get('configured') is True and knowledge.get('executor_running') is True)
                add(code, ok, 'Local endpoint reports ready.', 'Local endpoint does not report the required readiness.')
            except Exception:
                # Never include URL error reasons or response bodies in reports.
                add(code, False, '', 'Local probe unavailable, refused, invalid, or not ready; inspect service logs locally.')
    elif probe:
        add('live_probe', False, '', 'Probe refused because trial isolation was not confirmed.')
    else:
        warn('live_probe', 'Live endpoints were not queried; use --probe only after starting the trial services.')
    warn('public_access', 'Public access is NOT verified. Domain, gateway, TLS, authentication and external acceptance remain separate checks.')
    return checks


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--probe', action='store_true', help='Query only the isolated loopback trial endpoints.')
    parser.add_argument('--report', type=Path, help='Create a new .json report; existing files are never overwritten.')
    args = parser.parse_args(argv)
    if args.report is not None and (args.report.suffix.lower() != '.json' or args.report.exists()):
        print('Report requires a new .json path; no existing file may be overwritten.', file=sys.stderr)
        return 1
    checks = collect_checks(args.config, probe=args.probe)
    failed = any(item['status'] == 'fail' for item in checks)
    report = {'scope': 'local_trial_preflight', 'ready_for_public_access': False,
              'status': 'failed' if failed else ('passed_local_probe' if args.probe else 'passed_static_checks'),
              'checks': checks}
    if args.report is not None:
        try:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open('x', encoding='utf-8') as output:
                json.dump(report, output, indent=2, ensure_ascii=False)
                output.write('\n')
        except OSError:
            print('Report could not be created; existing files are never overwritten.', file=sys.stderr)
            return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
