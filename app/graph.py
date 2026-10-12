"""Neo4j access: schema, writes and read-only query helpers."""
from __future__ import annotations

from neo4j import GraphDatabase, RoutingControl

from .config import Settings

CONSTRAINTS = [
    ("PlaybookCollection", "name"),
    ("Playbook", "uuid"),
    ("Step", "key"),
    ("Connector", "name"),
    ("ConnectorOperation", "key"),
    ("Module", "name"),
    ("Document", "id"),
    ("Chunk", "id"),
    ("Concept", "name"),
    ("SolutionPack", "repo"),
    ("ChatSession", "id"),
    ("ChatMessage", "id"),
]

FULLTEXT = [
    "CREATE FULLTEXT INDEX chunk_text IF NOT EXISTS FOR (n:Chunk) ON EACH [n.text, n.doc_title]",
    "CREATE FULLTEXT INDEX entity_text IF NOT EXISTS "
    "FOR (n:Playbook|Step|Connector|ConnectorOperation|Module|SolutionPack) "
    "ON EACH [n.name, n.label, n.title, n.description, n.summary]",
]


def _vector_index(name: str, label: str, dim: int) -> str:
    return (
        f"CREATE VECTOR INDEX {name} IF NOT EXISTS FOR (n:{label}) ON (n.embedding) "
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: {dim}, `vector.similarity_function`: 'cosine'}}}}"
    )


def strip_embeddings(value):
    """Drop embedding vectors from query results before returning them to users/LLMs."""
    if isinstance(value, dict):
        return {k: strip_embeddings(v) for k, v in value.items() if k != "embedding"}
    if isinstance(value, list):
        return [strip_embeddings(v) for v in value]
    return value


def graph_node(node_id: str, labels: list[str], props: dict) -> dict:
    """Compact node shape for the UI: display label, type and a few safe properties."""
    kind = next((l for l in labels if l != "Meta"), "Node")
    props = {k: v for k, v in props.items() if k not in ("embedding", "text", "summary")}
    label = props.get("name") or props.get("title") or props.get("key") or props.get("id") or kind
    return {"id": node_id, "type": kind, "label": str(label), "props": props}


def _checked(method):
    import functools

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        self._guard()
        try:
            return method(self, *args, **kwargs)
        except Exception as exc:
            self._mark(exc)
            raise

    return wrapper


class Graph:
    def __init__(self, s: Settings):
        self.settings = s
        self.driver = GraphDatabase.driver(
            s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password), notifications_min_severity="OFF",
            connection_timeout=5.0, max_transaction_retry_time=4.0,  # fail fast when a workspace's database is down
        )
        self.database = s.neo4j_database
        self._down_until = 0.0
        self._checked_at = 0.0

    def close(self) -> None:
        self.driver.close()

    def _guard(self) -> None:
        """After a connection failure, fail fast for a few seconds instead of retrying on every call."""
        import time

        from neo4j.exceptions import ServiceUnavailable

        now = time.time()
        if self._down_until > now:
            raise ServiceUnavailable(f"{self.settings.neo4j_uri} was unreachable a moment ago")
        if now - self._checked_at > 30:  # cheap direct check (no transaction retries) every 30 s
            try:
                self.driver.verify_connectivity()
            except Exception as exc:
                self._down_until = now + 15
                raise ServiceUnavailable(f"{self.settings.neo4j_uri} is unreachable: {str(exc).splitlines()[0][:120]}")
            self._checked_at = now

    def _mark(self, exc: Exception) -> None:
        import time

        from neo4j.exceptions import ServiceUnavailable, SessionExpired

        if isinstance(exc, (ServiceUnavailable, SessionExpired)):
            self._down_until = time.time() + 15

    @_checked
    def run(self, cypher: str, **params) -> list[dict]:
        records, _, _ = self.driver.execute_query(cypher, params, database_=self.database)
        return [r.data() for r in records]

    @_checked
    def read(self, cypher: str, **params) -> list[dict]:
        records, _, _ = self.driver.execute_query(
            cypher, params, database_=self.database, routing_=RoutingControl.READ
        )
        return [r.data() for r in records]

    @_checked
    def read_paths(self, cypher: str, **params) -> dict:
        """Run a query returning `path` columns and flatten them into {nodes, edges} for visualisation."""
        records, _, _ = self.driver.execute_query(
            cypher, params, database_=self.database, routing_=RoutingControl.READ
        )
        nodes, edges = {}, {}
        for record in records:
            path = record["path"]
            for node in path.nodes:
                nodes[node.element_id] = graph_node(node.element_id, list(node.labels), dict(node))
            for rel in path.relationships:
                edges[rel.element_id] = {
                    "id": rel.element_id, "source": rel.start_node.element_id,
                    "target": rel.end_node.element_id, "type": rel.type,
                }
        return {"nodes": list(nodes.values()), "edges": list(edges.values())}

    @_checked
    def read_only(self, cypher: str, params: dict | None = None, limit: int = 200) -> list[dict]:
        """Run untrusted Cypher in a READ access-mode session (Neo4j rejects writes there)."""
        with self.driver.session(database=self.database, default_access_mode="READ") as session:
            def work(tx):
                result = tx.run(cypher, params or {})
                rows = []
                for record in result:
                    rows.append(strip_embeddings(record.data()))
                    if len(rows) >= limit:
                        break
                return rows

            return session.execute_read(work)

    # ------------------------------------------------------------ schema

    @_checked
    def ensure_schema(self) -> None:
        s = self.settings
        meta = self.run("MATCH (m:Meta {id: 'embeddings'}) RETURN m.provider AS provider, m.model AS model, m.dim AS dim")
        if meta and (meta[0]["model"], meta[0]["dim"]) != (s.embed_model, s.embed_dim):
            raise RuntimeError(
                f"Graph was embedded with {meta[0]['provider']}:{meta[0]['model']} ({meta[0]['dim']}d) but the "
                f"current config uses {s.embed_provider}:{s.embed_model} ({s.embed_dim}d). Vectors are not "
                "comparable across models. Run `python -m app.cli wipe --yes` and re-ingest, or use a separate database."
            )
        for label, prop in CONSTRAINTS:
            self.run(f"CREATE CONSTRAINT {label.lower()}_{prop} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE")
        self.run(_vector_index("playbook_embedding", "Playbook", s.embed_dim))
        self.run(_vector_index("chunk_embedding", "Chunk", s.embed_dim))
        for statement in FULLTEXT:
            self.run(statement)
        self.run(
            "MERGE (m:Meta {id: 'embeddings'}) SET m.provider = $provider, m.model = $model, m.dim = $dim",
            provider=s.embed_provider, model=s.embed_model, dim=s.embed_dim,
        )

    @_checked
    def wipe(self) -> None:
        # IN TRANSACTIONS needs an auto-commit transaction, so use session.run directly.
        with self.driver.session(database=self.database) as session:
            session.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS").consume()
        for row in self.run("SHOW INDEXES YIELD name, type WHERE type IN ['VECTOR', 'FULLTEXT'] RETURN name"):
            self.run(f"DROP INDEX {row['name']} IF EXISTS")

    @_checked
    def stats(self) -> list[dict]:
        return self.read(
            "MATCH (n) WHERE NOT (n:Meta OR n:ChatSession OR n:ChatMessage) "
            "RETURN labels(n)[0] AS label, count(*) AS count ORDER BY count DESC"
        )
