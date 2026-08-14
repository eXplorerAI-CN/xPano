from __future__ import annotations

import argparse
import html.parser
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path


def _wheel_tags(filename):
    if not filename.endswith(".whl"):
        return None
    try:
        _prefix, python_tag, abi_tag, platform_tag = filename[:-4].rsplit("-", 3)
    except ValueError:
        return None
    return python_tag, abi_tag, platform_tag


def _compatibility_score(filename, python_abi):
    tags = _wheel_tags(filename)
    if not tags:
        return -1
    python_tag, abi_tag, platform_tag = tags
    if platform_tag == "any" and abi_tag == "none" and ("py3" in python_tag or "py2.py3" in python_tag):
        return 10
    if platform_tag != "win_amd64":
        return -1
    if python_abi in python_tag and abi_tag == python_abi:
        return 40
    if abi_tag == "abi3":
        requested = int(python_abi[2:])
        supported = [int(value) for value in re.findall(r"cp(\d{2,3})", python_tag)]
        if supported and min(supported) <= requested:
            return 30
    return -1


def select_compatible_wheel(files, python_abi):
    candidates = []
    for item in files:
        if item.get("packagetype") != "bdist_wheel":
            continue
        score = _compatibility_score(item.get("filename", ""), python_abi)
        digest = (item.get("digests") or {}).get("sha256")
        if score >= 0 and item.get("url") and item.get("size") and digest:
            candidates.append((score, item["filename"], item))
    if not candidates:
        return None
    candidates.sort(key=lambda value: (-value[0], value[1]))
    return candidates[0][2]


class _LinkParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(href)


def _simple_links(html_text):
    parser = _LinkParser()
    parser.feed(html_text)
    return parser.links


def select_simple_index_wheel(html_text, package, version, python_abi):
    normalized_package = re.sub(r"[-_.]+", "-", package).lower()
    candidates = []
    for href in _simple_links(html_text):
        absolute = urllib.parse.urljoin("https://download.pytorch.org/", href)
        parsed = urllib.parse.urlparse(absolute)
        filename = urllib.parse.unquote(Path(parsed.path).name)
        normalized_filename = re.sub(r"[-_.]+", "-", filename).lower()
        if not normalized_filename.startswith(normalized_package + "-") or version.lower() not in filename.lower():
            continue
        score = _compatibility_score(filename, python_abi)
        fragment = urllib.parse.parse_qs(parsed.fragment)
        digest = fragment.get("sha256", [""])[0]
        if score >= 0 and re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            clean_url = urllib.parse.urlunparse(parsed._replace(fragment=""))
            candidates.append((score, filename, clean_url, digest.lower()))
    if not candidates:
        return None
    candidates.sort(key=lambda value: (-value[0], value[1]))
    _score, filename, url, digest = candidates[0]
    return {"filename": filename, "url": url, "sha256": digest}


def _fetch_text(url):
    request = urllib.request.Request(url, headers={"User-Agent": "xPano-manifest-generator/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def _fetch_json(url):
    return json.loads(_fetch_text(url))


def _content_length(url):
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "xPano-manifest-generator/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        value = response.headers.get("Content-Length")
    if not value or int(value) <= 0:
        raise RuntimeError(f"Artifact size was not provided by {url}")
    return int(value)


def _mirror_url(package, filename):
    normalized = re.sub(r"[-_.]+", "-", package).lower()
    page = f"https://pypi.tuna.tsinghua.edu.cn/simple/{normalized}/"
    try:
        for href in _simple_links(_fetch_text(page)):
            absolute = urllib.parse.urljoin(page, href)
            if urllib.parse.unquote(Path(urllib.parse.urlparse(absolute).path).name) == filename:
                return urllib.parse.urlunparse(urllib.parse.urlparse(absolute)._replace(fragment=""))
    except Exception:
        return ""
    return ""


def _pypi_artifact(name, version, python_abi):
    payload = _fetch_json(f"https://pypi.org/pypi/{urllib.parse.quote(name)}/{urllib.parse.quote(version)}/json")
    selected = select_compatible_wheel(payload.get("urls", []), python_abi)
    if not selected:
        raise RuntimeError(f"No compatible Windows x64 {python_abi} wheel for {name}=={version}")
    mirror = _mirror_url(name, selected["filename"])
    urls = [url for url in [mirror, selected["url"]] if url]
    return {
        "id": f"{re.sub(r'[-_.]+', '-', name).lower()}-{version}",
        "kind": "wheel",
        "package": name,
        "version": version,
        "filename": selected["filename"],
        "size": selected["size"],
        "sha256": selected["digests"]["sha256"].lower(),
        "urls": urls,
    }


def _pytorch_artifact(name, version, profile, python_abi):
    index = "cu128" if profile == "cuda" else "cpu"
    page = f"https://download.pytorch.org/whl/{index}/{name}/"
    selected = select_simple_index_wheel(_fetch_text(page), name, version, python_abi)
    if not selected:
        raise RuntimeError(f"No compatible PyTorch wheel for {name}=={version} ({profile})")
    return {
        "id": f"{name}-{version}-{profile}",
        "kind": "wheel",
        "package": name,
        "version": version,
        "filename": selected["filename"],
        "size": _content_length(selected["url"]),
        "sha256": selected["sha256"],
        "urls": [selected["url"]],
    }


def generate_manifest(lock):
    python_abi = lock.get("pythonAbi", "cp312")
    artifacts = []
    common_ids = []
    for name, version in lock["common"].items():
        artifact = _pypi_artifact(name, version, python_abi)
        artifacts.append(artifact)
        common_ids.append(artifact["id"])

    profiles = {}
    for profile_name in ["cpu", "cuda"]:
        profile_ids = list(common_ids)
        for name, version in lock["profiles"][profile_name].items():
            artifact = _pytorch_artifact(name, version, profile_name, python_abi)
            artifacts.append(artifact)
            profile_ids.append(artifact["id"])
        for model in lock.get("models", []):
            artifacts.append(dict(model)) if not any(item["id"] == model["id"] for item in artifacts) else None
            profile_ids.append(model["id"])
        profiles[profile_name] = {"artifacts": profile_ids}

    return {
        "schemaVersion": 1,
        "runtimeVersion": lock["runtimeVersion"],
        "platform": "windows-x86_64",
        "pythonAbi": python_abi,
        "profiles": profiles,
        "artifacts": artifacts,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate a hash-locked xPano densification runtime manifest without downloading wheels.")
    parser.add_argument("--lock", default="runtime/densify-package-lock.json")
    parser.add_argument("--output", default="runtime/densify-runtime-manifest.json")
    args = parser.parse_args(argv)
    lock = json.loads(Path(args.lock).read_text(encoding="utf-8"))
    manifest = generate_manifest(lock)
    Path(args.output).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
