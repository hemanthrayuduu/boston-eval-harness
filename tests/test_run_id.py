"""Run identity must fork on exactly the things that can move a number, and
nothing else."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from harness.config import RetrieverConfig, RunConfig, RunIdentity

MODEL = "claude-opus-5-2026-01-15"


def make_identity(**overrides) -> RunIdentity:
    base = {
        "config": RunConfig(model=MODEL),
        "snapshot_sha256": "a" * 64,
        "git_sha": "abc1234",
        "scorer_version": "1.0.0",
        "corpus_version": "2026-07-01",
        "replicate_index": 0,
    }
    config_overrides = overrides.pop("config_overrides", None)
    if config_overrides:
        base["config"] = RunConfig(model=MODEL, **config_overrides)
    base.update(overrides)
    return RunIdentity(**base)


class TestConfigHash:
    def test_is_stable_across_instances(self) -> None:
        assert RunConfig(model=MODEL).config_hash() == RunConfig(model=MODEL).config_hash()

    def test_tool_order_does_not_matter_but_tool_set_does(self) -> None:
        a = RunConfig(model=MODEL, tools_enabled=("query", "list_tables"))
        b = RunConfig(model=MODEL, tools_enabled=("list_tables", "query"))
        c = RunConfig(model=MODEL, tools_enabled=("list_tables",))
        assert a.config_hash() == b.config_hash()
        assert a.config_hash() != c.config_hash()

    @pytest.mark.parametrize(
        "overrides",
        [
            {"temperature": 0.7},
            {"scaffold": "single_shot"},
            {"step_budget": 25},
            {"prompt_template_id": "caveat_loaded"},
            {"retriever": RetrieverConfig(kind="hybrid")},
            {"retriever": RetrieverConfig(top_k=20)},
            {"retriever": RetrieverConfig(rerank=True)},
        ],
        ids=lambda o: next(iter(o)) + "=" + str(next(iter(o.values())))[:24],
    )
    def test_changes_when_any_knob_changes(self, overrides) -> None:
        assert RunConfig(model=MODEL, **overrides).config_hash() != RunConfig(
            model=MODEL
        ).config_hash()


class TestModelPinning:
    @pytest.mark.parametrize("alias", ["claude-opus-5", "gpt-5", "llama3", "sonnet"])
    def test_undated_alias_rejected(self, alias: str) -> None:
        # Aliases get repointed by providers. Accepting one means a run is not
        # reproducible and nothing in the trace would reveal why.
        with pytest.raises(ValidationError):
            RunConfig(model=alias)

    @pytest.mark.parametrize("pinned", ["claude-opus-5-2026-01-15", "gpt-5-20260114"])
    def test_dated_id_accepted(self, pinned: str) -> None:
        assert RunConfig(model=pinned).model == pinned

    def test_undated_model_accepted_when_digest_supplied(self) -> None:
        # Self-hosted models have no release date; the digest is the pin.
        config = RunConfig(model="qwen3-32b-instruct", model_digest="sha256:abc123")
        assert config.model_digest == "sha256:abc123"

    def test_digest_participates_in_config_hash(self) -> None:
        a = RunConfig(model="qwen3-32b-instruct", model_digest="sha256:aaa")
        b = RunConfig(model="qwen3-32b-instruct", model_digest="sha256:bbb")
        assert a.config_hash() != b.config_hash()


class TestRunId:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"snapshot_sha256": "b" * 64},
            {"git_sha": "def5678"},
            {"git_dirty": True},
            {"scorer_version": "1.1.0"},
            {"corpus_version": "2026-08-01"},
            {"replicate_index": 1},
            {"config_overrides": {"temperature": 0.7}},
        ],
        ids=lambda o: next(iter(o)),
    )
    def test_forks_on_every_identity_component(self, overrides) -> None:
        assert make_identity().run_id != make_identity(**overrides).run_id

    def test_is_stable_when_nothing_changes(self) -> None:
        assert make_identity().run_id == make_identity().run_id

    def test_replicates_differ_in_run_id_but_share_config_hash(self) -> None:
        """The variance experiment needs both halves of this.

        v2's `run_id = sha256(config)` made five identical runs collide on one ID,
        so run-to-run stddev could not be recorded at all.
        """
        replicates = [make_identity(replicate_index=i) for i in range(5)]
        assert len({r.run_id for r in replicates}) == 5
        assert len({r.config_hash for r in replicates}) == 1


class TestComparability:
    def test_same_provenance_is_comparable(self) -> None:
        assert make_identity().comparable_with(make_identity(config_overrides={"step_budget": 3}))

    @pytest.mark.parametrize(
        "overrides",
        [{"corpus_version": "2026-08-01"}, {"scorer_version": "2.0.0"}, {"snapshot_sha256": "c" * 64}],
        ids=lambda o: next(iter(o)),
    )
    def test_shifted_ground_truth_is_not_comparable(self, overrides) -> None:
        # Diffing across these silently compares scores computed against
        # different answer keys. That is how a regression gate lies.
        assert not make_identity().comparable_with(make_identity(**overrides))
