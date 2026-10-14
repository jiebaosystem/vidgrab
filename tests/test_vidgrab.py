"""Offline tests. Fixtures mirror real actor output (2026-10), trimmed to the fields vidgrab reads.

Run: python3 -m unittest discover -s tests
"""

import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import vidgrab  # noqa: E402

DOUYIN = [{
    "url": "https://v.douyin.com/qTaYgDk_lwY/",
    "result": {
        "source": "douyin", "id": "7695006267269467418", "author": "示例作者",
        "title": "示例视频标题", "error": False,
        "medias": [
            {"url": "https://v11-o.douyinvod.com/hd.mp4", "height": 720, "quality": "hd_no_watermark", "extension": "mp4", "type": "video"},
            {"url": "https://v11-o.douyinvod.com/nowm.mp4", "height": 720, "quality": "no_watermark", "extension": "mp4", "type": "video"},
            {"url": "https://v5-hl.zjcdn.com/wm.mp4", "height": 720, "quality": "watermark", "extension": "mp4", "type": "video"},
            {"url": "https://sf11-cdn-tos.douyinstatic.com/a.mp3", "quality": "audio", "extension": "mp3", "type": "audio"},
        ],
    },
}]


def yt(fid, kind, quality, height, mime, audio_quality="None", ext="mp4", bitrate=0, **extra):
    return dict({"url": "https://redirector.googlevideo.com/videoplayback?itag=%s" % fid, "formatId": fid, "type": kind,
                 "quality": quality, "height": str(height) if height else "None", "mimeType": mime,
                 "audioQuality": audio_quality, "extension": ext, "bitrate": str(bitrate)}, **extra)


YOUTUBE = [{
    "url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
    "result": {
        "source": "youtube", "title": "Big Buck Bunny", "error": False,
        "medias": [
            yt("18", "video", "mp4 (360p)", 360, 'video/mp4; codecs="avc1.42001E, mp4a.40.2"', "AUDIO_QUALITY_LOW", is_audio=True),
            yt("315", "video", "webm (2160p60)", 2160, 'video/webm; codecs="vp9"', ext="webm", bitrate=26523399),
            yt("299", "video", "mp4 (1080p60)", 1080, 'video/mp4; codecs="avc1.64002A"', bitrate=6933529),
            yt("303", "video", "webm (1080p60)", 1080, 'video/webm; codecs="vp9"', ext="webm", bitrate=4652291),
            yt("298", "video", "mp4 (720p60)", 720, 'video/mp4; codecs="avc1.4D4020"', bitrate=4206005),
            yt("136", "video", "mp4 (720p)", 720, 'video/mp4; codecs="avc1.4D401F"', bitrate=2496430),
            yt("140", "audio", "m4a (131kb/s)", 0, 'audio/mp4; codecs="mp4a.40.2"', "AUDIO_QUALITY_MEDIUM", ext="m4a", bitrate=130992),
            yt("251", "audio", "opus (144kb/s)", 0, 'audio/webm; codecs="opus"', "AUDIO_QUALITY_MEDIUM", ext="opus", bitrate=143908),
            yt("258", "audio", "m4a (390kb/s)", 0, 'audio/mp4; codecs="mp4a.40.2"', "AUDIO_QUALITY_HIGH", ext="m4a", bitrate=389665),
            yt("328", "audio", "m4a (385kb/s)", 0, 'audio/mp4; codecs="ec-3"', "AUDIO_QUALITY_HIGH", ext="m4a", bitrate=385274),
        ],
    },
}]


def medias(fixture):
    return vidgrab.parse_result(fixture)[1]


def itag(m):
    return m["url"].rsplit("=", 1)[1]


class TestLinks(unittest.TestCase):
    def test_extract_from_share_text(self):
        share = ("4.82 :7pm jca:/ 11/09 A@T.Lj 示例视频标题 "
                 "https://v.douyin.com/qTaYgDk_lwY/ 复制此链接，打开Dou音搜索，直接观看视频！")
        self.assertEqual(vidgrab.extract_url(share), "https://v.douyin.com/qTaYgDk_lwY/")
        self.assertEqual(vidgrab.extract_url("看这个https://x.com/a/status/1，好看"), "https://x.com/a/status/1")
        self.assertEqual(vidgrab.extract_url("(https://youtu.be/abc)."), "https://youtu.be/abc")
        with self.assertRaises(vidgrab.VidgrabError):
            vidgrab.extract_url("no link here")

    def test_platform_and_actor(self):
        cases = {
            "https://v.douyin.com/x/": "douyin", "https://www.iesdouyin.com/share/video/1": "douyin",
            "https://vm.tiktok.com/x/": "tiktok", "https://www.tiktok.com/@a/video/1": "tiktok",
            "https://youtu.be/x": "youtube", "https://m.youtube.com/watch?v=x": "youtube",
            "https://x.com/a/status/1": "x", "https://twitter.com/a/status/1": "x",
            "https://vimeo.com/1": "other", "https://notx.com/a": "other",
        }
        for url, platform in cases.items():
            self.assertEqual(vidgrab.detect_platform(url), platform, url)
        self.assertEqual(vidgrab.actor_for("douyin"), vidgrab.ACTOR_DOUYIN)
        self.assertEqual(vidgrab.actor_for("x"), vidgrab.ACTOR_GENERIC)
        self.assertEqual(vidgrab.actor_input(vidgrab.ACTOR_GENERIC, "u")["link"], "u")
        self.assertEqual(vidgrab.actor_input(vidgrab.ACTOR_DOUYIN, "u")["links"], ["u"])


class TestChoose(unittest.TestCase):
    def test_douyin_takes_hd_without_watermark(self):
        pick = vidgrab.choose(medias(DOUYIN))
        self.assertEqual(pick["video"]["url"], "https://v11-o.douyinvod.com/hd.mp4")
        self.assertIsNone(pick["audio"])

    def test_youtube_with_ffmpeg_merges_1080p_h264_and_aac(self):
        pick = vidgrab.choose(medias(YOUTUBE), can_merge=True)
        self.assertEqual(itag(pick["video"]), "299")  # not 4K, not VP9
        self.assertEqual(itag(pick["audio"]), "140")  # AAC ~128k: not E-AC-3, opus or the 390k stream

    def test_youtube_max_height(self):
        pick = vidgrab.choose(medias(YOUTUBE), max_height=720, can_merge=True)
        self.assertEqual(itag(pick["video"]), "298")

    def test_youtube_without_ffmpeg_falls_back_to_stream_with_sound(self):
        pick = vidgrab.choose(medias(YOUTUBE), can_merge=False)
        self.assertEqual(itag(pick["video"]), "18")
        self.assertIsNone(pick["audio"])
        self.assertIn("ffmpeg", pick["note"])

    def test_resolver_error_and_empty(self):
        with self.assertRaises(vidgrab.VidgrabError):
            vidgrab.parse_result([{"result": {"error": "Video not found", "medias": []}}])
        with self.assertRaises(vidgrab.VidgrabError):
            vidgrab.parse_result([{"result": {"error": False, "medias": []}}])
        with self.assertRaises(vidgrab.VidgrabError):
            vidgrab.parse_result([])


class TestClean(unittest.TestCase):
    def test_only_own_old_files(self):
        with tempfile.TemporaryDirectory() as d:
            old = time.time() - 8 * 86400
            for name, mtime in [("vidgrab-old.mp4", old), ("vidgrab-new.mp4", time.time()),
                                ("someone-else.mp4", old), ("vidgrab-old.mp4.part", old)]:
                p = os.path.join(d, name)
                open(p, "w").close()
                os.utime(p, (mtime, mtime))
            removed = vidgrab.clean(d, 7)
            self.assertEqual(sorted(removed), ["vidgrab-old.mp4", "vidgrab-old.mp4.part"])
            self.assertEqual(sorted(os.listdir(d)), ["someone-else.mp4", "vidgrab-new.mp4"])
        self.assertEqual(vidgrab.clean("/nonexistent/vidgrab", 7), [])


class _RangeServer:
    """Local HTTP server serving one blob, with or without Range support; counts requests."""

    def __init__(self, blob, ranges=True):
        import http.server
        import threading
        outer = self
        self.requests = 0

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.requests += 1
                rng = self.headers.get("Range")
                if ranges and rng:
                    start, end = (int(x) for x in rng.split("=")[1].split("-"))
                    if start >= len(blob):
                        self.send_response(416)
                        self.end_headers()
                        return
                    body = blob[start:end + 1]
                    self.send_response(206)
                    self.send_header("Content-Range", "bytes %d-%d/%d" % (start, start + len(body) - 1, len(blob)))
                else:
                    body = blob
                    self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d/v.mp4" % self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class TestFetch(unittest.TestCase):
    blob = os.urandom(25000)

    def fetch(self, ranges):
        srv = _RangeServer(self.blob, ranges=ranges)
        try:
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "out.mp4")
                vidgrab.fetch_to(srv.url, path, chunk=10000)
                with open(path, "rb") as f:
                    data = f.read()
                self.assertEqual(os.listdir(d), ["out.mp4"])  # no .part left behind
            return data, srv.requests
        finally:
            srv.close()

    def test_chunked_ranges_reassemble_exactly(self):
        data, requests = self.fetch(ranges=True)
        self.assertEqual(data, self.blob)
        self.assertEqual(requests, 3)  # 10000 + 10000 + 5000

    def test_server_ignoring_range_still_works(self):
        data, requests = self.fetch(ranges=False)
        self.assertEqual(data, self.blob)
        self.assertEqual(requests, 1)

    def test_http_error_is_a_download_error(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(vidgrab.VidgrabError) as ctx:
                vidgrab.fetch_to("http://127.0.0.1:9/nothing.mp4", os.path.join(d, "x.mp4"))
            self.assertEqual(ctx.exception.stage, "download")
            self.assertEqual(os.listdir(d), [])


class TestMain(unittest.TestCase):
    def run_main(self, argv, env):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), redirect_stdout(buf):
            code = vidgrab.main(argv)
        return code, json.loads(buf.getvalue())

    def test_download_writes_file_and_prints_json(self):
        with tempfile.TemporaryDirectory() as d:
            def fake_fetch(url, path, referer=None):
                with open(path, "wb") as f:
                    f.write(b"video")
            with mock.patch.object(vidgrab, "call_apify", return_value=DOUYIN) as api, \
                    mock.patch.object(vidgrab, "fetch_to", side_effect=fake_fetch):
                code, out = self.run_main(["4.82 看看 https://v.douyin.com/qTaYgDk_lwY/ 复制此链接"],
                                          {"APIFY_TOKEN": "t", "VIDGRAB_DIR": d})
            self.assertEqual(code, 0)
            self.assertEqual(api.call_args[0][0], vidgrab.ACTOR_DOUYIN)
            self.assertTrue(out["ok"])
            self.assertEqual(out["platform"], "douyin")
            self.assertEqual(out["play_url"], "https://v11-o.douyinvod.com/hd.mp4")
            self.assertEqual(out["source_url"], "https://v.douyin.com/qTaYgDk_lwY/")
            self.assertTrue(os.path.basename(out["file"]).startswith("vidgrab-"))
            self.assertTrue(out["file"].endswith("-douyin-7695006267269467418.mp4"))
            self.assertEqual(out["size"], 5)

    def test_resolve_only_downloads_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(vidgrab, "call_apify", return_value=DOUYIN), \
                    mock.patch.object(vidgrab, "fetch_to") as fetch:
                code, out = self.run_main(["resolve", "https://v.douyin.com/x/"], {"APIFY_TOKEN": "t", "VIDGRAB_DIR": d})
            self.assertEqual(code, 0)
            self.assertNotIn("file", out)
            fetch.assert_not_called()

    def test_failure_is_json_with_stage(self):
        with tempfile.TemporaryDirectory() as d:
            code, out = self.run_main(["https://v.douyin.com/x/"], {"APIFY_TOKEN": "", "VIDGRAB_DIR": d})
        self.assertEqual(code, 1)
        self.assertEqual(out, {"ok": False, "stage": "config", "error": out["error"]})
        self.assertIn("APIFY_TOKEN", out["error"])


if __name__ == "__main__":
    unittest.main()
