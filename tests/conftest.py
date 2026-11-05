"""Shared test setup.

* Puts src/ and tools/ on the import path (the scripts import each other by module name).
* Stubs optional runtime packages (mssql_python) so tests run without them; nothing here talks to Azure.
* load_notebook() pulls selected top-level functions/constants out of a Fabric notebook source file and
  executes only those, so notebook helpers can be tested without running the whole notebook.
* spark fixture: a local Spark + Delta Lake session (same Spark 3.5 / Delta 3.2 line as Fabric Runtime 1.3).
  Spark tests are skipped automatically when Java or pyspark is not available (they always run in CI).
"""
import ast, os, shutil, sys, types
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("src", "tools", "checks"):
    p = os.path.join(ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import mssql_python  # noqa: F401
except Exception:  # SQL endpoint driver (and its native libs) is only needed for live runs
    sys.modules["mssql_python"] = types.SimpleNamespace(connect=lambda *a, **k: None)

NOTEBOOKS = os.path.join(ROOT, "notebooks")


def load_notebook(name, names, namespace=None):
    """Execute only the named top-level defs / assignments from notebooks/<name>.py, in file order."""
    path = name if os.path.isabs(name) else os.path.join(NOTEBOOKS, name)
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    ns = dict(namespace or {})
    wanted, found = set(names), set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            targets = {node.name}
        elif isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
        else:
            continue
        hit = targets & wanted
        if hit:
            exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), ns)
            found |= hit
    missing = wanted - found
    assert not missing, f"{missing} not found in {path}"
    return ns


@pytest.fixture
def no_sleep(monkeypatch):
    """Make polling loops instant."""
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)


def _spark_available():
    try:
        import pyspark  # noqa: F401
        import delta  # noqa: F401
    except ImportError:
        return False
    return bool(shutil.which("java") or os.environ.get("JAVA_HOME"))


# CI sets REQUIRE_SPARK=1 so the Spark tests can never be skipped silently there.
if os.environ.get("REQUIRE_SPARK") == "1" and not _spark_available():
    raise RuntimeError("REQUIRE_SPARK=1 but pyspark/delta-spark/Java are not available")

requires_spark = pytest.mark.skipif(not _spark_available(), reason="needs pyspark, delta-spark and Java (runs in CI)")


@pytest.fixture(scope="session")
def spark(tmp_path_factory):
    if not _spark_available():
        pytest.skip("needs pyspark, delta-spark and Java (runs in CI)")
    from delta import configure_spark_with_delta_pip
    from pyspark.sql import SparkSession
    wh = str(tmp_path_factory.mktemp("warehouse"))
    builder = (SparkSession.builder.master("local[1]").appName("nyc-taxi-tests")
               .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
               .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
               .config("spark.sql.warehouse.dir", wh)
               .config("spark.sql.shuffle.partitions", "1")
               .config("spark.ui.enabled", "false")
               .config("spark.sql.session.timeZone", "UTC"))
    session = configure_spark_with_delta_pip(builder).getOrCreate()
    for schema in ("bronze", "silver", "gold"):
        session.sql(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    yield session
    session.stop()
