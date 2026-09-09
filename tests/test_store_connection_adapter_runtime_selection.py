import os
import unittest
from unittest.mock import patch

import app.domains.store_connection.adapters as adapters
from app.domains.store_connection.adapters.coupang import CoupangConnectionAdapter
from app.domains.store_connection.adapters.coupang_production import (
    CoupangProductionAdapter,
)


class StoreConnectionAdapterRuntimeSelectionTests(unittest.TestCase):
    def test_source_runtime_defaults_to_fake(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(
            adapters.sys, "frozen", False, create=True,
        ):
            self.assertIsInstance(adapters.get_adapter("COUPANG"), CoupangConnectionAdapter)

    def test_frozen_runtime_defaults_to_production(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(
            adapters.sys, "frozen", True, create=True,
        ):
            self.assertIsInstance(adapters.get_adapter("COUPANG"), CoupangProductionAdapter)

    def test_explicit_fake_override_supports_packaged_isolation_tests(self):
        with patch.dict(
            os.environ, {"HOMEZ_STORE_CONNECTION_ADAPTER_MODE": "FAKE"}, clear=True,
        ), patch.object(adapters.sys, "frozen", True, create=True):
            self.assertIsInstance(adapters.get_adapter("COUPANG"), CoupangConnectionAdapter)


if __name__ == "__main__":
    unittest.main()
