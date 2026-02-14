import tempfile
import time
import unittest
from pathlib import Path

from photohub.services.preload import PIL_AVAILABLE, DiskImageCache, PreviewPrefetchManager

if PIL_AVAILABLE:
    from PIL import Image
else:
    Image = None


@unittest.skipUnless(PIL_AVAILABLE, "Pillow requis pour les tests cache/prefetch.")
class PreviewPrefetchTests(unittest.TestCase):
    def _create_test_image(self, path: Path, color: tuple[int, int, int]) -> None:
        assert Image is not None
        img = Image.new("RGB", (1400, 900), color=color)
        img.save(path, format="JPEG", quality=90)
        img.close()

    def test_disk_cache_creates_and_reuses_entries(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src.jpg"
            self._create_test_image(src, (120, 50, 40))

            cache = DiskImageCache(root / "cache", max_cache_bytes=128 * 1024 * 1024, min_free_bytes=1)
            p1 = cache.get_or_create_cached_path(src, kind="preview", width=1600, height=1600)
            self.assertIsNotNone(p1)
            assert p1 is not None
            self.assertTrue(p1.exists())

            p2 = cache.get_existing_cached_path(src, kind="preview", width=1600, height=1600)
            self.assertIsNotNone(p2)
            assert p2 is not None
            self.assertEqual(p1, p2)

    def test_prefetch_manager_warms_next_asset(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a = root / "a.jpg"
            b = root / "b.jpg"
            self._create_test_image(a, (20, 80, 120))
            self._create_test_image(b, (60, 140, 40))

            manager = PreviewPrefetchManager(root / "cache", depth=3, worker_count=2)
            try:
                manager.update_sequence([str(a), str(b)])
                manager.on_selected_index(0)
                data = None
                for _ in range(30):
                    data = manager.get_warmed_preview_bytes(b)
                    if data:
                        break
                    time.sleep(0.05)
                self.assertTrue(bool(data))
            finally:
                manager.shutdown()


if __name__ == "__main__":
    unittest.main()

