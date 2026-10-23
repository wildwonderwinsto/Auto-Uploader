"""Daily limits: up to 100 a day on YouTube (schema 14) and each Buffer platform's own maximum."""

import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from shortspublisher.library import SCHEMA_VERSION, Library, LibraryError
from shortspublisher.limits_schema import LOOSENED
from shortspublisher.social import FACEBOOK, INSTAGRAM, TIKTOK

FIXTURES = Path(__file__).parent / "fixtures"


def checks(db: sqlite3.Connection) -> dict[str, str]:
    return {
        table: db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        for table, _ in LOOSENED
    }


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "library.sqlite3"

    def as_version_13(self, plans: bool = True) -> None:
        """A schema-13 library with a recurring plan that four tables may reference."""
        Library(self.path).initialize()
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("BEGIN")
            db.execute("UPDATE publishing_control SET daily_limit=3")  # schema 13's default
            version = db.execute("PRAGMA schema_version").fetchone()[0]
            db.execute("PRAGMA writable_schema=ON")
            for table, column in LOOSENED:
                sql = checks(db)[table]
                db.execute(
                    "UPDATE sqlite_master SET sql=? WHERE type='table' AND name=?",
                    (
                        sql.replace(f"{column} BETWEEN 1 AND 100", f"{column} BETWEEN 1 AND 10"),
                        table,
                    ),
                )
            db.execute(f"PRAGMA schema_version={version + 1}")
            db.execute("PRAGMA writable_schema=OFF")
            db.execute("PRAGMA user_version=13")
            db.execute("COMMIT")
        if plans:
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("PRAGMA foreign_keys=ON")
                db.execute(
                    "INSERT INTO recurring_plans VALUES ('p','Plan','youtube','UC1','Ch','{}',1,0,1,1,"
                    "'t','c','t','t','2030-01-01T00:00:00+00:00','w',3)"
                )
                db.execute(
                    "INSERT INTO recurring_items VALUES ('i','p',1,'UC1','h','{}',0,'Queued','','t',"
                    "'b',NULL,'')"
                )
                db.execute("UPDATE publishing_control SET daily_limit=4")
            with (
                closing(sqlite3.connect(self.path)) as db,
                self.assertRaises(sqlite3.IntegrityError),
            ):
                db.execute("UPDATE publishing_control SET daily_limit=15")

    def test_version_13_loosens_to_100_and_keeps_every_row_and_reference(self):
        self.as_version_13()
        with closing(sqlite3.connect(self.path)) as db:
            before = {
                t: db.execute(f"SELECT * FROM {t}").fetchall()
                for t in ("recurring_plans", "recurring_items", "publishing_control")
            }
        Library(self.path).initialize()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            for table, rows in before.items():
                if table != "publishing_control":
                    self.assertEqual(db.execute(f"SELECT * FROM {table}").fetchall(), rows, table)
            # No daily limit of our own any more: the global value becomes YouTube's 100. The
            # approved plan keeps the cap it was approved with.
            self.assertEqual(
                db.execute("SELECT daily_limit FROM publishing_control").fetchone()[0], 100
            )
            self.assertEqual(
                db.execute("SELECT approved_limit FROM recurring_plans").fetchone()[0], 3
            )
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            db.execute("UPDATE publishing_control SET daily_limit=100")
            db.execute("UPDATE recurring_plans SET approved_limit=15")
            for statement in (
                "UPDATE publishing_control SET daily_limit=101",
                "UPDATE recurring_plans SET approved_limit=0",
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)

    def test_oldest_fixture_reaches_the_new_limits(self):
        shutil.copyfile(FIXTURES / "library-v1.sqlite3", self.path)
        Library(self.path).initialize()
        with closing(sqlite3.connect(self.path)) as db:
            for table, column in LOOSENED:
                self.assertIn(f"CHECK({column} BETWEEN 1 AND 100)", checks(db)[table])
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_unexpected_definition_changes_nothing(self):
        self.as_version_13(plans=False)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("BEGIN")
            version = db.execute("PRAGMA schema_version").fetchone()[0]
            db.execute("PRAGMA writable_schema=ON")
            sql = checks(db)["recurring_plans"].replace("BETWEEN 1 AND 10", "BETWEEN 1 AND 12")
            db.execute(
                "UPDATE sqlite_master SET sql=? WHERE type='table' AND name='recurring_plans'",
                (sql,),
            )
            db.execute(f"PRAGMA schema_version={version + 1}")
            db.execute("PRAGMA writable_schema=OFF")
            db.execute("COMMIT")
            before = checks(db)
        with self.assertRaises(LibraryError):
            Library(self.path).initialize()
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 13)
            self.assertEqual(checks(db), before)  # the publishing_control edit was rolled back
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")


class PlatformMaximumTests(unittest.TestCase):
    def test_each_platform_allows_at_least_fifteen_a_day(self):
        self.assertEqual(
            {p.name: p.daily_max for p in (TIKTOK, INSTAGRAM, FACEBOOK)},
            {"TikTok": 25, "Instagram": 50, "Facebook": 35},
        )


if __name__ == "__main__":
    unittest.main()
