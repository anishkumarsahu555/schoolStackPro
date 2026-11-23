"""Resolve deployment configurations with synthetic settings and no daemon."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ComposeTests(unittest.TestCase):
    def config(self, settings, engine=""):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "custom file.env"
            config.write_text("SECRET_KEY=fixture\nDB_PASSWORD=fixture\n" + settings)
            result = subprocess.run(
                ["bash", "-ec", """
                    repo=$PWD
                    fail() { echo "$*" >&2; exit 1; }
                    source scripts/docker-common.sh
                    configure_stack "$1" "$2"
                    "${compose[@]}" config --format json
                """, "config-test", str(config), engine],
                cwd=ROOT, capture_output=True, text=True, check=True,
                env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]},
            )
            return json.loads(result.stdout)

    def test_vps_environment(self):
        for stage in ("staging", "production"):
            config = self.config(f"PROJECT_NAME=schools\nDEPLOY_ENV={stage}\nVPS_STORAGE_ROOT=/srv/apps\nPORT=8123\n")
            self.assertEqual(config["name"], "schools-" + stage)
            services = config["services"]
            web = services["web"]
            self.assertEqual(web["ports"][0]["target"], 8123)
            self.assertEqual(web["ports"][0]["published"], "8123")
            base = f"/srv/apps/schools/{stage}"
            mounts = {v["target"]: v["source"] for v in web["volumes"]}
            self.assertEqual(mounts, {"/srv/media": base + "/media", "/data": base + "/data"})
            self.assertEqual(services["postgres"]["volumes"][0]["source"], base + "/postgres")
            self.assertNotIn("ports", services["postgres"])
            self.assertEqual(web["image"], services["storage-init"]["image"])
            self.assertEqual(web["volumes"], services["migrate"]["volumes"])

    def test_reference_environment_variables(self):
        settings = ("DB_ENGINE=sqlite\nDATABASE_ENGINE=postgres\nPORT=8127\n"
                    "IMAGE_NAME=schools-web\nAPP_IMAGE_TAG=staging\n"
                    "PYTHON_IMAGE=python:fixture\nUV_IMAGE=uv:fixture\n")
        web = self.config(settings)["services"]["web"]
        self.assertEqual(web["environment"]["DB_ENGINE"], "postgres")
        self.assertEqual(web["image"], "schools-web:staging")
        self.assertEqual(web["ports"][0]["target"], 8127)
        self.assertEqual(web["build"]["args"]["PYTHON_IMAGE"], "python:fixture")
        self.assertEqual(web["build"]["args"]["UV_IMAGE"], "uv:fixture")
        self.assertEqual(self.config(settings, "sqlite")["services"]["web"]["environment"]["DB_ENGINE"], "sqlite")
        self.assertNotIn("postgres", self.config("DB_ENGINE=sqlite\n")["services"])

    def test_deployment_and_web_settings(self):
        config = self.config(
            "COMPOSE_PROJECT_NAME=fixture\nAPP_IMAGE=schools:reviewed\n"
            "APP_PORT=8742\nHOST_BIND=127.0.0.2\nVPS_STORAGE_ROOT=/srv/fixture\n"
            "WEB_WORKERS=3\nWEB_TIMEOUT=175\nWEB_MEMORY_LIMIT=512m\n"
        )
        web = config["services"]["web"]
        self.assertEqual(config["name"], "fixture")
        for service in ("web", "migrate", "storage-init"):
            self.assertEqual(config["services"][service]["image"], "schools:reviewed")
        port, = web["ports"]
        self.assertEqual((port["host_ip"], port["published"], port["target"]), ("127.0.0.2", "8742", 8742))
        self.assertEqual(web["environment"]["APP_PORT"], "8742")
        self.assertEqual(web["environment"]["WEB_WORKERS"], "3")
        self.assertEqual(web["environment"]["WEB_TIMEOUT"], "175")
        self.assertEqual(int(web["mem_limit"]), 512 * 1024 * 1024)
        self.assertIn("/srv/fixture/schoolstackpro/production/media",
                      [mount["source"] for mount in web["volumes"]])

    def test_original_volumes_and_optional_sqlite(self):
        config = self.config("")
        self.assertEqual(config["name"], "schoolstackpro")
        self.assertEqual(config["services"]["postgres"]["volumes"][0]["source"], "postgres_data")
        self.assertIn("media", [v["source"] for v in config["services"]["web"]["volumes"]])
        sqlite = self.config("APP_DATA_ROOT=/tmp/schools-test\nDB_PASSWORD=\n", "sqlite")
        self.assertNotIn("postgres", sqlite["services"])
        self.assertEqual(sqlite["services"]["web"]["environment"]["SQLITE_PATH"], "/data/db.sqlite3")
        self.assertEqual(sqlite["services"]["web"]["environment"]["db"], "sqlite")


if __name__ == "__main__":
    unittest.main()
