"""Initial schema for the PR Governance Harness."""

import sqlalchemy as sa
from alembic import op


revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("repository", sa.String(255), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("diff", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("budget", sa.JSON(), nullable=False),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("execution_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_runs_repository", "runs", ["repository"])
    op.create_index("ix_runs_status", "runs", ["status"])
    op.create_table(
        "node_executions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("node", sa.String(64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", "node", "attempt", name="uq_run_node_attempt"),
    )
    op.create_index("ix_node_executions_run_id", "node_executions", ["run_id"])
    op.create_table(
        "trace_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("node", sa.String(64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_trace_events_run_id", "trace_events", ["run_id"])
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("repository", sa.String(255), nullable=False),
        sa.Column("label", sa.String(32), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_feedback_run_id", "feedback", ["run_id"])
    op.create_index("ix_feedback_repository", "feedback", ["repository"])
    op.create_index("ix_feedback_fingerprint", "feedback", ["fingerprint"])
    op.create_table(
        "memory_patterns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("repository", sa.String(255), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("examples", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("repository", "fingerprint", name="uq_memory_repository_fingerprint"),
    )
    op.create_index("ix_memory_patterns_repository", "memory_patterns", ["repository"])
    op.create_index("ix_memory_patterns_fingerprint", "memory_patterns", ["fingerprint"])
    op.create_table(
        "evolution_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("repository", sa.String(255), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default="prompt"),
        sa.Column("status", sa.String(32), nullable=False, server_default="candidate"),
        sa.Column("candidate", sa.JSON(), nullable=False),
        sa.Column("validation", sa.JSON(), nullable=False),
        sa.Column("holdout", sa.JSON(), nullable=False),
        sa.Column("predecessor_version", sa.String(64), nullable=True),
        sa.Column("gate_decision", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("repository", "version", name="uq_evolution_repository_version"),
    )
    op.create_index("ix_evolution_versions_repository", "evolution_versions", ["repository"])
    op.create_table(
        "webhook_deliveries",
        sa.Column("delivery_id", sa.String(255), primary_key=True),
        sa.Column("repository", sa.String(255), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_webhook_deliveries_repository", "webhook_deliveries", ["repository"])
    op.create_table(
        "benchmark_cases",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("split", sa.String(16), nullable=False),
        sa.Column("language", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="none"),
        sa.Column("diff", sa.Text(), nullable=False),
        sa.Column("expected", sa.JSON(), nullable=False),
    )
    op.create_index("ix_benchmark_cases_split", "benchmark_cases", ["split"])


def downgrade() -> None:
    for table in ("benchmark_cases", "webhook_deliveries", "evolution_versions", "memory_patterns", "feedback", "trace_events", "node_executions", "runs"):
        op.drop_table(table)
