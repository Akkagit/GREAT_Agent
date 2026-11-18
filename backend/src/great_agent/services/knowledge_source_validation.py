"""Validate live citation availability and the physical immutable original."""
import hashlib

from great_agent.db.knowledge_chat_repository import KnowledgeChatValidationError
from great_agent.db.knowledge_repository import KnowledgeNotFound


class KnowledgeSourceValidator:
    def __init__(self, repository, ingestion):
        self.repository, self.ingestion = repository, ingestion

    def __call__(self, conversation_id, message_id, source_id):
        citation = self.repository.get_citation(conversation_id, message_id, source_id)
        if not citation.get('available'):
            raise KnowledgeChatValidationError('citation is unavailable')
        try:
            path, document = self.ingestion.original(citation['document_id'])
            if document['blob']['sha256'] != citation['source_sha256']:
                raise ValueError('identity mismatch')
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                while block := stream.read(1024 * 1024):
                    digest.update(block)
            if digest.hexdigest() != citation['source_sha256']:
                raise ValueError('original identity mismatch')
        except (OSError, ValueError, KnowledgeNotFound, KeyError):
            raise KnowledgeChatValidationError('citation original is unavailable') from None
        # The scope may have changed while hashing a large original.
        latest = self.repository.get_citation(conversation_id, message_id, source_id)
        if not latest.get('available') or any(latest.get(k) != citation.get(k) for k in (
                'id', 'version_id', 'source_sha256', 'evidence_text', 'evidence_text_sha256')):
            raise KnowledgeChatValidationError('citation is unavailable')
        return latest
