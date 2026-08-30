import pytest

from app.core.contracts import LayerResult
from app.layers.base import Layer, VerificationInput


class DummyLayer(Layer):
    name = "dummy"

    async def run(self, verification_input: VerificationInput) -> LayerResult:
        return LayerResult(
            layer=self.name,
            risk=0.0,
            confidence=1.0,
            ok=True,
            reason="dummy always passes",
            demonstrator=True,
        )


@pytest.mark.asyncio
async def test_dummy_layer_returns_valid_layer_result():
    layer = DummyLayer()
    result = await layer.run(VerificationInput(user_ref="user-1"))

    assert isinstance(result, LayerResult)
    assert result.layer == "dummy"
    assert result.ok is True
    assert result.demonstrator is True


def test_layer_is_abstract():
    with pytest.raises(TypeError):
        Layer()
