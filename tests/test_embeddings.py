"""EMBEDDING_PROVIDER selects OpenAI, or the local model when the key is not configured."""

from app.infrastructure.embeddings import LOCAL_MODEL, OPENAI_MODEL, LocalEmbedder, OpenAIEmbedder, embedder_from_env


def test_placeholder_openai_key_uses_the_local_model(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "your_openai_api_key_here")
    monkeypatch.setenv("EMBEDDING_MODEL", OPENAI_MODEL)

    embedder = embedder_from_env()

    assert isinstance(embedder, LocalEmbedder)
    assert embedder._model_name == LOCAL_MODEL


def test_missing_openai_key_uses_the_local_model(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)

    embedder = embedder_from_env()

    assert isinstance(embedder, LocalEmbedder)
    assert embedder._model_name == LOCAL_MODEL


def test_local_provider_uses_the_configured_local_model(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("EMBEDDING_MODEL", LOCAL_MODEL)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-key")

    embedder = embedder_from_env()

    assert isinstance(embedder, LocalEmbedder)
    assert embedder._model_name == LOCAL_MODEL


def test_openai_provider_with_a_key_uses_openai(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-key")
    monkeypatch.setenv("EMBEDDING_MODEL", OPENAI_MODEL)

    embedder = embedder_from_env()

    assert isinstance(embedder, OpenAIEmbedder)
    assert embedder._model == OPENAI_MODEL
    assert embedder._api_key == "sk-real-key"
