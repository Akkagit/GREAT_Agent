"""Durable conversation history, state, runs and lifecycle events."""
from alembic import op
from great_agent.db.conversation_models import ConversationModel, ConversationRunModel, ConversationMessageModel, ConversationEventModel
revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

def upgrade():
    bind=op.get_bind()
    for model in (ConversationModel,ConversationRunModel,ConversationMessageModel,ConversationEventModel):
        model.__table__.create(bind,checkfirst=True)

def downgrade():
    bind=op.get_bind()
    for model in (ConversationEventModel,ConversationMessageModel,ConversationRunModel,ConversationModel):
        model.__table__.drop(bind,checkfirst=True)
