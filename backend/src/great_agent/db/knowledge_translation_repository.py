"""Persistent, derived cache; authorization always belongs to the source validator."""
from sqlalchemy.dialects.sqlite import insert

from great_agent.db.knowledge_translation_models import KnowledgeCitationTranslationModel as Translation


class KnowledgeTranslationRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    def get(self, key):
        with self.sessions() as session:
            row = session.get(Translation, key)
            return row.translated_text if row else None

    def put(self, key, citation, target_language, model_fingerprint, translation_version, translated_text):
        with self.sessions.begin() as session:
            session.execute(insert(Translation).values(
                cache_key=key, citation_id=citation['id'], version_id=citation['version_id'],
                source_sha256=citation['source_sha256'], evidence_text_sha256=citation['evidence_text_sha256'],
                target_language=target_language, model_fingerprint=model_fingerprint,
                translation_version=translation_version, translated_text=translated_text,
            ).on_conflict_do_nothing(index_elements=['cache_key']))
