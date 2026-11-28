"""Create PostgreSQL-backed governed dementia knowledge store.

Revision ID: 20261128_01_dementia_knowledge_store
Revises: 20261127_02_merge_first_use_and_companion_turn_scope
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261128_01_dementia_knowledge_store"
down_revision = "20261127_02_merge_first_use_and_companion_turn_scope"
branch_labels = None
depends_on = None


SOURCES = (
    ("nia_dementia", "U.S. National Institute on Aging", "Alzheimer's Disease and Related Dementias", "https://www.nia.nih.gov/health/alzheimers-and-dementia", "public_health_guidance", "United States", None, None, "catalogued_not_ingested", "Dementia types, symptoms, diagnosis, treatment context and caregiving."),
    ("alz_association", "Alzheimer's Association", "Alzheimer's Association", "https://www.alz.org/", "caregiver_education", "United States", None, None, "catalogued_not_ingested", "Family caregiving education, safety, communication and support."),
    ("who_isupport", "World Health Organization", "iSupport for Dementia: Training and support manual for carers", "https://www.who.int/publications/i/item/9789241515863", "public_health_guidance", "International", sa.date(2019, 5, 9), None, "catalogued_not_ingested", "Dementia fundamentals, caregiver wellbeing, everyday care and behaviour changes."),
    ("nice_ng97", "National Institute for Health and Care Excellence", "Dementia: assessment, management and support (NG97)", "https://www.nice.org.uk/guidance/ng97", "clinical_guideline", "United Kingdom", sa.date(2018, 6, 20), sa.date(2025, 10, 24), "catalogued_not_ingested", "Clinical guideline covering assessment, person-centred care, coordination, interventions and carer support."),
)

DOMAINS = (
    ("fundamentals", "Dementia fundamentals", "Types, terminology, diagnosis boundaries and general course.", 1),
    ("cognition", "Cognitive changes", "Memory, orientation, language, attention and recognition.", 2),
    ("behaviour", "Behavioural and psychological changes", "Distress, agitation, withdrawal, suspicion and repetitive behaviour.", 3),
    ("communication", "Communication", "Listening, accessible language, reassurance and non-verbal communication.", 4),
    ("personal_care", "Personal care", "Bathing, dressing, grooming, toileting, autonomy and dignity.", 5),
    ("nutrition", "Nutrition and swallowing", "Eating, appetite, swallowing concerns and weight-related observations.", 6),
    ("hydration", "Hydration", "Drinking patterns, reported refusal and hydration-related concerns.", 7),
    ("sleep", "Sleep", "Night waking, routine changes and sleep-related concerns.", 8),
    ("mobility", "Mobility and falls", "Mobility baseline, falls, balance and functional support.", 9),
    ("safety", "Safety and supervision", "Wandering, driving, household safety, financial vulnerability and supervision.", 10),
    ("medical", "Medical considerations", "Medication information boundaries, pain, acute changes and professional assessment.", 11),
    ("emotional_wellbeing", "Emotional wellbeing", "Fear, anxiety, frustration, loneliness, identity and meaningful activity.", 12),
    ("caregiver_wellbeing", "Caregiver wellbeing", "Caregiver strain, respite, family responsibilities and support.", 13),
    ("long_term_care", "Long-term care and transitions", "Care coordination, transitions, advance preferences and future planning.", 14),
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("Dementia knowledge persistence requires PostgreSQL.")

    op.create_table(
        "dementia_knowledge_domains",
        sa.Column("domain_id", sa.String(64), primary_key=True),
        sa.Column("title", sa.String(180), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "dementia_knowledge_sources",
        sa.Column("source_id", sa.String(80), primary_key=True),
        sa.Column("publisher", sa.String(240), nullable=False),
        sa.Column("title", sa.String(400), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("evidence_class", sa.String(48), nullable=False),
        sa.Column("jurisdiction", sa.String(80), nullable=False),
        sa.Column("published_on", sa.Date(), nullable=True),
        sa.Column("publisher_reviewed_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="catalogued_not_ingested"),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_dementia_knowledge_sources_status", "dementia_knowledge_sources", ["status"])
    op.create_table(
        "dementia_knowledge_source_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(80), sa.ForeignKey("dementia_knowledge_sources.source_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_version", sa.String(120), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("extraction_method", sa.String(80), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("publisher_published_on", sa.Date(), nullable=True),
        sa.Column("publisher_reviewed_on", sa.Date(), nullable=True),
        sa.Column("review_status", sa.String(40), nullable=False, server_default="ingested_not_reviewed"),
        sa.Column("reviewed_by", sa.String(180), nullable=True),
        sa.Column("reviewer_qualification", sa.String(180), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_due_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source_id", "content_sha256", name="uq_dementia_knowledge_source_revision_hash"),
    )
    op.create_index("ix_dementia_knowledge_source_revision_source_time", "dementia_knowledge_source_revisions", ["source_id", "retrieved_at"])
    op.create_index("ix_dementia_knowledge_source_revision_status", "dementia_knowledge_source_revisions", ["review_status", "review_due_on"])
    op.create_table(
        "dementia_knowledge_source_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_revision_id", sa.String(36), sa.ForeignKey("dementia_knowledge_source_revisions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("reviewer_id", sa.String(180), nullable=False),
        sa.Column("reviewer_qualification", sa.String(180), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("review_due_on", sa.Date(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_dementia_knowledge_source_reviews_revision_time", "dementia_knowledge_source_reviews", ["source_revision_id", "reviewed_at"])
    op.create_table(
        "dementia_knowledge_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("item_key", sa.String(120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("domain_id", sa.String(64), sa.ForeignKey("dementia_knowledge_domains.domain_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("evidence_class", sa.String(48), nullable=False),
        sa.Column("jurisdiction", sa.String(80), nullable=False),
        sa.Column("applicable_context", sa.Text(), nullable=False),
        sa.Column("limitations", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("reviewed_by", sa.String(180), nullable=True),
        sa.Column("reviewer_qualification", sa.String(180), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_due_on", sa.Date(), nullable=True),
        sa.Column("supersedes_item_id", sa.String(36), sa.ForeignKey("dementia_knowledge_items.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("item_key", "version", name="uq_dementia_knowledge_item_key_version"),
    )
    op.create_index("ix_dementia_knowledge_item_status_domain", "dementia_knowledge_items", ["status", "domain_id"])
    op.create_index("ix_dementia_knowledge_item_review_due", "dementia_knowledge_items", ["status", "review_due_on"])
    op.create_table(
        "dementia_knowledge_item_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("knowledge_item_id", sa.String(36), sa.ForeignKey("dementia_knowledge_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_revision_id", sa.String(36), sa.ForeignKey("dementia_knowledge_source_revisions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("supporting_excerpt", sa.Text(), nullable=False),
        sa.Column("source_locator", sa.String(240), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("knowledge_item_id", "source_revision_id", name="uq_dementia_knowledge_item_source_revision"),
    )
    op.create_index("ix_dementia_knowledge_item_source_item", "dementia_knowledge_item_sources", ["knowledge_item_id"])
    op.create_table(
        "dementia_knowledge_reviews",
        "dementia_knowledge_source_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("knowledge_item_id", sa.String(36), sa.ForeignKey("dementia_knowledge_items.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("reviewer_id", sa.String(180), nullable=False),
        sa.Column("reviewer_qualification", sa.String(180), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("review_due_on", sa.Date(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_dementia_knowledge_reviews_item_time", "dementia_knowledge_reviews", ["knowledge_item_id", "reviewed_at"])
    op.execute("""
        CREATE FUNCTION reject_dementia_knowledge_review_mutation()
        RETURNS trigger AS $
        BEGIN
            RAISE EXCEPTION 'Dementia knowledge review records are append-only';
        END;
        $ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_dementia_knowledge_reviews_immutable
        BEFORE UPDATE OR DELETE ON dementia_knowledge_reviews
        FOR EACH ROW EXECUTE FUNCTION reject_dementia_knowledge_review_mutation()
    """)
    op.execute("""
        CREATE TRIGGER trg_dementia_knowledge_source_reviews_immutable
        BEFORE UPDATE OR DELETE ON dementia_knowledge_source_reviews
        FOR EACH ROW EXECUTE FUNCTION reject_dementia_knowledge_review_mutation()
    """)
    op.execute("""
        CREATE FUNCTION protect_dementia_knowledge_source_revision_content()
        RETURNS trigger AS $
        BEGIN
            IF NEW.source_id IS DISTINCT FROM OLD.source_id
               OR NEW.source_version IS DISTINCT FROM OLD.source_version
               OR NEW.content_text IS DISTINCT FROM OLD.content_text
               OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256
               OR NEW.extraction_method IS DISTINCT FROM OLD.extraction_method
               OR NEW.retrieved_at IS DISTINCT FROM OLD.retrieved_at
               OR NEW.publisher_published_on IS DISTINCT FROM OLD.publisher_published_on THEN
                RAISE EXCEPTION 'Ingested dementia knowledge source content is immutable; ingest a new revision';
            END IF;
            RETURN NEW;
        END;
        $ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_dementia_knowledge_source_revision_content_immutable
        BEFORE UPDATE ON dementia_knowledge_source_revisions
        FOR EACH ROW EXECUTE FUNCTION protect_dementia_knowledge_source_revision_content()
    """)

    domains = [dict(domain_id=x[0], title=x[1], description=x[2], display_order=x[3]) for x in DOMAINS]
    sources = [dict(source_id=x[0], publisher=x[1], title=x[2], canonical_url=x[3], evidence_class=x[4], jurisdiction=x[5], published_on=x[6], publisher_reviewed_on=x[7], status=x[8], scope=x[9]) for x in SOURCES]
    op.bulk_insert(sa.table("dementia_knowledge_domains", sa.column("domain_id"), sa.column("title"), sa.column("description"), sa.column("display_order")), domains)
    op.bulk_insert(sa.table("dementia_knowledge_sources", sa.column("source_id"), sa.column("publisher"), sa.column("title"), sa.column("canonical_url"), sa.column("evidence_class"), sa.column("jurisdiction"), sa.column("published_on"), sa.column("publisher_reviewed_on"), sa.column("status"), sa.column("scope")), sources)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    inspector = inspect(bind)
    op.execute("DROP TRIGGER IF EXISTS trg_dementia_knowledge_source_revision_content_immutable ON dementia_knowledge_source_revisions")
    op.execute("DROP FUNCTION IF EXISTS protect_dementia_knowledge_source_revision_content()")
    op.execute("DROP TRIGGER IF EXISTS trg_dementia_knowledge_source_reviews_immutable ON dementia_knowledge_source_reviews")
    op.execute("DROP TRIGGER IF EXISTS trg_dementia_knowledge_reviews_immutable ON dementia_knowledge_reviews")
    op.execute("DROP FUNCTION IF EXISTS reject_dementia_knowledge_review_mutation()")
    for table in (
        "dementia_knowledge_reviews",
        "dementia_knowledge_item_sources",
        "dementia_knowledge_items",
        "dementia_knowledge_source_revisions",
        "dementia_knowledge_sources",
        "dementia_knowledge_domains",
    ):
        if inspector.has_table(table):
            op.drop_table(table)
