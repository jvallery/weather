# SPDX-License-Identifier: MIT
"""Asset visibility moves to `weather.visible_asset`; public views stop nesting a barrier view.

Expand-only on top of 0017_obs_import_rapid. Inline DDL; never import application modules here.

`public_api.videos`, `public_api.keograms` and `public_api.stills` joined `public_api.assets`, a
`security_barrier` view, inside per-row laterals. The barrier stops Postgres from pushing
`a.id = pa.asset_id` into the asset scan, so every video, keogram and still re-read every public
asset. Cost grew with rows x assets: on 2026-11-04 the catalog took about 6 s and the web's
bounded read gave up, so the site said "Recording API unavailable" while everything else was
healthy (stills took 17 s).

The visibility rule itself is unchanged, and now has one home: `weather.visible_asset` (no
barrier, no grants) is `public_api.assets`'s definition as it stands, and `public_api.assets`
selects from it, keeping its barrier for the API role. The three dependent views are replaced
column for column with `public_api.assets` swapped for `weather.visible_asset`. Their current
definitions are read from the catalog in the database (a DO block, so `--sql` renders it too) with
`search_path` set to `pg_catalog`, so every name is schema-qualified and the swap is exact; a
definition without the reference fails the migration.
"""

from alembic import op

revision = "0018_visible_asset"
down_revision = "0017_obs_import_rapid"
branch_labels = None
depends_on = None

ASSET_COLUMNS = "id,class,sha256,extension,bytes,content_type,width,height,public_not_before"

UPGRADE = f"""DO $$
DECLARE
  saved text := current_setting('search_path');
  body text;
  name text;
BEGIN
  PERFORM set_config('search_path', 'pg_catalog', true);
  EXECUTE 'CREATE VIEW weather.visible_asset AS '
    || rtrim(pg_get_viewdef('public_api.assets'::regclass), E'; \\n');
  FOREACH name IN ARRAY ARRAY['videos', 'keograms', 'stills'] LOOP
    body := rtrim(pg_get_viewdef(('public_api.' || name)::regclass), E'; \\n');
    IF position('public_api.assets' IN body) = 0 THEN
      RAISE EXCEPTION USING MESSAGE = 'public_api.' || name || ' no longer reads public_api.assets';
    END IF;
    EXECUTE 'CREATE OR REPLACE VIEW public_api.' || quote_ident(name)
      || ' WITH (security_barrier=true) AS '
      || replace(body, 'public_api.assets', 'weather.visible_asset');
  END LOOP;
  EXECUTE 'CREATE OR REPLACE VIEW public_api.assets WITH (security_barrier=true) AS '
    || 'SELECT {ASSET_COLUMNS} FROM weather.visible_asset';
  PERFORM set_config('search_path', saved, true);
END
$$"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    raise RuntimeError("Production downgrade is disabled; use expand/contract or PITR")
