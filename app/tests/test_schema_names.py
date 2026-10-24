# SPDX-License-Identifier: MIT
from weather.db.models import metadata


def test_constraint_and_backing_index_names_do_not_collide() -> None:
    schema_indexes: set[str] = set()
    for table in metadata.sorted_tables:
        names = [str(constraint.name) for constraint in table.constraints if constraint.name]
        assert len(names) == len(set(names)), table.name
        # PostgreSQL backing indexes share the schema's relation-name namespace.
        for constraint in table.constraints:
            if constraint.__class__.__name__ in {"UniqueConstraint", "PrimaryKeyConstraint"}:
                name = str(constraint.name)
                assert name not in schema_indexes, name
                schema_indexes.add(name)
        for index in table.indexes:
            assert index.name not in schema_indexes, index.name
            schema_indexes.add(str(index.name))
