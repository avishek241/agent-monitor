"""The frozen contract (design §17): schemas copied verbatim, store implements store_api, routes listed."""
import filecmp
import inspect
import unittest
from pathlib import Path

from deck import store, store_api, views_types

ROOT = Path(__file__).resolve().parent.parent


class TestContract(unittest.TestCase):
    def test_schemas_identical_to_design(self):
        names = sorted(p.name for p in (ROOT / "docs/v2/schemas").glob("*.json"))
        self.assertEqual(names, sorted(p.name for p in (ROOT / "deck/schemas").glob("*.json")))
        for n in names:
            self.assertTrue(filecmp.cmp(ROOT / "docs/v2/schemas" / n, ROOT / "deck/schemas" / n, shallow=False), n)

    def test_store_implements_api(self):
        pairs = [(store_api.ProjectStoreAPI, store.ProjectStore), (store_api.GlobalStoreAPI, store.GlobalStore)]
        for api, impl in pairs:
            self.assertTrue(issubclass(impl, api))
            for name, fn in inspect.getmembers(api, inspect.isfunction):
                mine = getattr(impl, name)
                self.assertIsNot(mine, fn, f"{impl.__name__}.{name} not implemented")
                want = [p for p in inspect.signature(fn).parameters]
                have = [p for p in inspect.signature(mine).parameters]
                self.assertEqual(have[:len(want)], want, f"{impl.__name__}.{name} signature drifted")

    def test_sub_apis(self):
        from deck.domains import Domains
        for name, fn in inspect.getmembers(store_api.DomainsAPI, inspect.isfunction):
            self.assertTrue(hasattr(Domains, name), name)
        for api, impl in ((store_api.UsageAPI, store._Usage), (store_api.LimitsAPI, store._Limits),
                          (store_api.LanesAPI, store._Lanes), (store_api.AccountStateAPI, store._AccountState),
                          (store_api.UsageCostAPI, store._UsageCost),
                          (store_api.SlotsAPI, store._Slots)):
            for name, fn in inspect.getmembers(api, inspect.isfunction):
                self.assertTrue(hasattr(impl, name), f"{impl.__name__}.{name}")

    def test_routes_and_actions(self):
        self.assertEqual(len(views_types.ROUTES), 8)
        self.assertIn("reassign", views_types.ACTIONS)


if __name__ == "__main__":
    unittest.main()
