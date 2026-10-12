from app.chat import _answer_playbooks, _format_history


class FakeGraph:
    def read(self, cypher, **params):
        return [{"uuid": "u-iso", "name": "Endpoint - Isolate Host"},
                {"uuid": "u-dis", "name": "Disable User Account"},
                {"uuid": "u-pur", "name": "Phishing - Purge Malicious Email"}]


class FakeSvc:
    graph = FakeGraph()


def test_diagram_playbooks_include_named_but_uncited():
    answer = ("'Phishing - Purge Malicious Email' has an approval gate [C1]. "
              "Your 'Endpoint - Isolate Host' playbook waits for Tier 2 approval [D2]. "
              "The disable user account playbook also asks first [D4].")
    cited = [{"label": "C1", "type": "playbook", "uuid": "u-pur", "name": "Phishing - Purge Malicious Email"},
             {"label": "D2", "type": "internal_doc", "title": "Host isolation approval policy"}]
    names = [p["name"] for p in _answer_playbooks(FakeSvc(), answer, cited)]
    # cited first, then named ones (case-insensitive), no duplicates
    assert names == ["Phishing - Purge Malicious Email", "Endpoint - Isolate Host", "Disable User Account"]


def test_history_strips_old_citation_labels():
    text = _format_history([{"role": "assistant", "content": "Blocked by Block IP Address [C1][V2]."}])
    assert text == "ASSISTANT: Blocked by Block IP Address ."


def test_embedding_matches_needs_floor_and_clear_lead():
    from app.ingest.docs import embedding_matches
    c = lambda *scores: [{"uuid": str(i), "score": s} for i, s in enumerate(scores)]
    assert [x["uuid"] for x in embedding_matches(c(0.92, 0.888, 0.88))] == ["0"]      # clear winner
    assert embedding_matches(c(0.903, 0.902, 0.897)) == []                             # ambiguous
    assert embedding_matches(c(0.89, 0.85)) == []                                      # below floor
    assert [x["uuid"] for x in embedding_matches(c(0.924, 0.923, 0.907))] == ["0", "1"]  # tied pair, clear lead


def test_friendly_errors():
    from app.chat import friendly_error
    quota = Exception("429 RESOURCE_EXHAUSTED. {'error': {'message': 'Please retry in 2h15m15.011s.'}}")
    msg = friendly_error(quota)
    assert "quota" in msg and "2h15m15s" in msg and "{" not in msg
    assert "API key" in friendly_error(Exception("401 Unauthorized: invalid API key"))


def test_embedding_cache_only_embeds_missing(tmp_path):
    from app.providers import CachedEmbedder

    class Inner:
        dim = 3
        calls = []
        def embed(self, texts, query=False):
            self.calls.append(list(texts))
            return [[float(len(t)), 0.0, 1.0] for t in texts]

    inner = Inner()
    cache = CachedEmbedder(inner, tmp_path / "c.sqlite", "test:model")
    assert cache.embed(["a", "bb"]) == [[1.0, 0.0, 1.0], [2.0, 0.0, 1.0]]
    assert cache.embed(["bb", "ccc"]) == [[2.0, 0.0, 1.0], [3.0, 0.0, 1.0]]
    assert inner.calls == [["a", "bb"], ["ccc"]]            # "bb" came from the cache
    assert CachedEmbedder(Inner(), tmp_path / "c.sqlite", "other:model").embed(["a"]) == [[1.0, 0.0, 1.0]]


def test_graph_only_mode_makes_no_model_calls():
    from types import SimpleNamespace

    from app.chat import chat_events
    from app.retrieval import keyword_terms

    assert keyword_terms("Which playbooks use VirusTotal to block an IP?") == ["VirusTotal", "block", "IP"]

    class Boom:
        def __getattr__(self, name):
            raise AssertionError(f"model API used: {name}")

    class Store:
        def get(self, sid): return {"messages": [{"role": "user"}], "title": "t"}
        def history(self, sid, n): return [{"role": "user", "content": "earlier question"}]
        def add(self, *a, **k): return "m1"
        def rename(self, *a): pass

    hits = {"playbooks": [{"id": "u1", "name": "Block IP", "why": ["connector virustotal"]}],
            "vendor": [], "internal": [], "terms": ["virustotal"]}
    retriever = SimpleNamespace(
        search_keyword=lambda q, f=None: hits, search_all=lambda q: Boom().search_all,
        expand=lambda h: {**h, "linked": []},
        assemble=lambda h: ("ctx", [{"label": "C1", "type": "playbook", "uuid": "u1", "name": "Block IP"}]))
    svc = SimpleNamespace(chats=Store(), retriever=retriever, llm=Boom(), embedder=Boom(),
                          settings=SimpleNamespace(chat_history_turns=6),
                          playbook_graph=lambda uuids, steps: {"nodes": [1], "edges": []})
    events = list(chat_events(svc, "s1", "and virustotal?", mode="graph"))
    assert events[-1]["type"] == "done" and events[-1]["playbooks"] == [{"uuid": "u1", "name": "Block IP"}]
    text = "".join(e["text"] for e in events if e["type"] == "token")
    assert "Graph-only" in text and "Block IP" in text and "connector virustotal" in text
