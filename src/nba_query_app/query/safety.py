"""SQL safety checker: decides whether AI-written SQL may be run at all.

The SQL is parsed into a tree with sqlglot (no regexes), and every part of the
tree is checked against allowlists: only what's listed here is accepted, so
anything new or unexpected is refused by default.

A query passes only if it is:
- a single statement, and a plain read: SELECT (optionally with WITH, UNION,
  INTERSECT or EXCEPT). No writes anywhere, not even inside a WITH, no
  SELECT ... INTO (which creates a table), no row locks (FOR UPDATE), no
  recursive WITH (an easy way to make a query run forever);
- reading only the tables and views in ALLOWED_TABLES, in the `nba` schema.
  Postgres's own system tables (pg_catalog, information_schema) are refused,
  since they describe the server, users and settings;
- calling only the functions in ALLOWED_FUNCTION_TYPES and
  ALLOWED_FUNCTION_NAMES: everyday aggregates, window, maths, text and date
  functions. That excludes pg_sleep, generate_series, set_config,
  query_to_xml (which runs a query hidden in a string) and the like;
- no casts to the reg* types (e.g. 'pg_roles'::regclass), which look up
  system objects by name.

This is the first of several layers. The query also runs as the read-only
database user, in a read-only transaction, with a time limit and a row cap
(see run.py), so a gap here still can't change data or run for long.
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

# Longer than any sensible answer to a question; also keeps the parser's work bounded.
MAX_SQL_LENGTH = 10_000

# Every table and view in the nba schema. A test checks this against the database.
ALLOWED_SCHEMA = "nba"
ALLOWED_TABLES = frozenset(
    {
        "games",
        "meta",
        "player_details",
        "player_game_stats",
        "player_season_stats",
        "players",
        "playoff_series",
        "stat_coverage",
        "stat_tracked_since",
        "team_game_stats",
        "teams",
    }
)

# Functions sqlglot recognises get their own node type. Some operators are function
# nodes too (AND, OR, CASE's branches, ~ and ~*), so they are listed here.
ALLOWED_FUNCTION_TYPES: tuple[type[exp.Func], ...] = (
    # Logic and conditions
    exp.And,
    exp.Or,
    exp.Case,
    exp.If,
    exp.Coalesce,
    exp.Nullif,
    exp.Greatest,
    exp.Least,
    exp.Exists,
    exp.Cast,
    exp.Array,  # ARRAY[...], e.g. season = ANY(ARRAY['2015-16', '2016-17'])
    # Aggregates
    exp.Count,
    exp.Sum,
    exp.Avg,
    exp.Min,
    exp.Max,
    exp.Median,
    exp.Mode,
    exp.PercentileCont,
    exp.PercentileDisc,
    exp.Stddev,
    exp.StddevPop,
    exp.StddevSamp,
    exp.Variance,
    exp.VariancePop,
    exp.Corr,
    exp.LogicalAnd,  # bool_and
    exp.LogicalOr,  # bool_or
    exp.ArrayAgg,
    exp.GroupConcat,  # string_agg
    # Window functions
    exp.RowNumber,
    exp.Rank,
    exp.DenseRank,
    exp.PercentRank,
    exp.CumeDist,
    exp.Ntile,
    exp.Lag,
    exp.Lead,
    exp.FirstValue,
    exp.LastValue,
    exp.NthValue,
    # Maths
    exp.Abs,
    exp.Round,
    exp.Floor,
    exp.Ceil,
    exp.Trunc,
    exp.Sign,
    exp.Sqrt,
    exp.Pow,
    exp.Exp,
    exp.Ln,
    exp.Log,
    # Text
    exp.Lower,
    exp.Upper,
    exp.Initcap,
    exp.Length,
    exp.Trim,
    exp.Substring,
    exp.StrPosition,  # position(), strpos()
    exp.SplitPart,
    exp.Left,
    exp.Right,
    exp.Replace,
    exp.Concat,
    exp.ConcatWs,
    exp.RegexpLike,  # ~
    exp.RegexpILike,  # ~*
    # Dates
    exp.CurrentDate,
    exp.CurrentTimestamp,  # now()
    exp.Extract,  # also date_part()
    exp.DateTrunc,
    exp.TimestampTrunc,  # date_trunc()
    exp.TimeToStr,  # to_char()
    exp.MakeInterval,
    exp.JustifyInterval,
)

# Postgres functions sqlglot doesn't recognise, by name (lowercase).
ALLOWED_FUNCTION_NAMES = frozenset({"age", "make_date"})

# Statement types that write or change something. The top of the tree must be a
# query, but a write can hide inside one, e.g. WITH d AS (DELETE ... RETURNING *).
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.DML,  # INSERT, UPDATE, DELETE, MERGE
    exp.DDL,  # CREATE, DROP, ALTER, ...
    exp.Into,  # SELECT ... INTO creates a table
    exp.Lock,  # FOR UPDATE / FOR SHARE lock rows
    exp.Command,  # anything sqlglot couldn't parse properly (VACUUM, SHOW, EXPLAIN, ...)
    exp.Copy,
    exp.Set,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Grant,
    exp.Revoke,
    exp.TruncateTable,
)


class UnsafeSqlError(ValueError):
    """The SQL was refused. The message says why, in words fit to show the AI."""


def check_sql(sql: str) -> None:
    """Raise UnsafeSqlError unless `sql` is a single read-only query over the nba tables."""
    if len(sql) > MAX_SQL_LENGTH:
        raise UnsafeSqlError(f"The query is longer than {MAX_SQL_LENGTH} characters.")
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except SqlglotError as e:
        raise UnsafeSqlError(f"The query couldn't be parsed: {e}") from None
    except RecursionError:  # brackets nested ~45 deep; real queries use under 10
        raise UnsafeSqlError("The query is nested too deeply.") from None
    if len(statements) != 1:
        raise UnsafeSqlError(f"Expected exactly one statement, got {len(statements)}.")
    tree = statements[0]
    if not isinstance(tree, exp.Select | exp.SetOperation):
        raise UnsafeSqlError(f"Only SELECT queries are allowed, not {tree.key.upper()}.")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeSqlError(f"{node.key.upper()} isn't allowed; only reading is.")
    _check_ctes(tree)
    _check_tables(tree)
    _check_functions(tree)
    for node in tree.find_all(exp.ObjectIdentifier):
        raise UnsafeSqlError(f"Casting to {node.name} isn't allowed.")


def _check_ctes(tree: exp.Expression) -> None:
    for with_ in tree.find_all(exp.With):
        if with_.args.get("recursive"):
            raise UnsafeSqlError("WITH RECURSIVE isn't allowed.")


def _cte_names(tree: exp.Expression) -> set[str]:
    names = set()
    for cte in tree.find_all(exp.CTE):
        name = _key(cte.args["alias"].this)
        # Postgres looks in pg_catalog before anything else, so a pg_ name could
        # end up meaning a system table in a part of the query the WITH doesn't cover.
        if name.startswith("pg_"):
            raise UnsafeSqlError(f"WITH names can't start with pg_ (got {name!r}).")
        names.add(name)
    return names


def _check_tables(tree: exp.Expression) -> None:
    ctes = _cte_names(tree)
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            continue  # a function in FROM, e.g. generate_series(...): checked as a function
        name = _key(table.this)
        schema = _key(table.args["db"]) if table.args.get("db") else None
        shown = f"{schema}.{name}" if schema else name
        if table.args.get("catalog"):
            allowed = False
        elif schema is None:
            allowed = name in ALLOWED_TABLES or name in ctes
        else:
            allowed = schema == ALLOWED_SCHEMA and name in ALLOWED_TABLES
        if not allowed:
            raise UnsafeSqlError(
                f"Table {shown} isn't allowed; only tables in the {ALLOWED_SCHEMA} schema are."
            )


def _check_functions(tree: exp.Expression) -> None:
    for func in tree.find_all(exp.Func):
        if isinstance(func.parent, exp.Dot) and func.parent.expression is func:
            raise UnsafeSqlError(f"Schema-qualified function calls aren't allowed: {func.parent}.")
        if isinstance(func, exp.Anonymous):
            allowed = func.name.lower() in ALLOWED_FUNCTION_NAMES
        else:
            allowed = isinstance(func, ALLOWED_FUNCTION_TYPES)
        if not allowed:
            raise UnsafeSqlError(f"Function {_function_name(func)} isn't allowed.")


def _function_name(func: exp.Func) -> str:
    if isinstance(func, exp.Anonymous):
        return func.name
    return func.sql(dialect="postgres").split("(")[0]


def _key(identifier: exp.Expression) -> str:
    """A name as Postgres sees it: unquoted names are case-insensitive."""
    if isinstance(identifier, exp.Identifier) and identifier.quoted:
        return identifier.name
    return identifier.name.lower()
