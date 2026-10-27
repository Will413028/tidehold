import httpx2 as httpx

import run
from tidehold.platform.settings import ModelKeys
from tidehold.policy.providers import Claude


def test_the_benchmark_runs_a_claude_model_the_product_does_not_run_yet() -> None:
    keys = ModelKeys(anthropic="k", anthropic_workspace="wrk_1")
    # Building sends nothing, so the client is never opened.
    provider = run._provider("anthropic:claude-haiku-5-5", httpx.AsyncClient(), keys)

    assert isinstance(provider, Claude)
    assert (provider.model, provider.workspace) == ("claude-haiku-5-5", "wrk_1")
    assert run.SPACING_S > 0
