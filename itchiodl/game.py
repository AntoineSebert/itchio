import re
import json
import urllib
import datetime
from pathlib import Path
from sys import argv
import requests

from itchiodl import utils


class Game:
    """Representation of a game download"""

    def __init__(self, data):
        self.args: list[str] = argv[1:]
        if "--human-folders" in self.args:
            self.humanFolders = True
        else:
            self.humanFolders = False

        self.data = data.get("game")
        self.name = self.data.get("title")
        self.publisher = self.data.get("user").get("username")
        self.link = self.data.get("url")

        if "game_id" in data:
            self.id = data.get("id")
            self.game_id = data.get("game_id")
        else:
            self.id = False
            self.game_id = self.data["id"]

        matches = re.match(r"https://(.+)\.itch\.io/(.+)", self.link)
        self.game_slug = matches.group(2)
        if self.humanFolders:
            self.game_slug = utils.clean_path(self.data.get("title"))
            self.publisher_slug = self.data.get("user").get("display_name")
            # This Branch covers the case that the user has
            # not set a display name, and defaults to their username
            if not self.publisher_slug:
                self.publisher_slug = self.data.get("user").get("username")
        else:
            self.publisher_slug = matches.group(1)

        self.files = []
        self.downloads = []
        self.dir = (
            Path(".")
            / utils.clean_path(self.publisher_slug)
            / utils.clean_path(self.game_slug)
        )

    def load_downloads(self, token):
        """Load all downloads for this game"""
        self.downloads = []
        if self.id:
            response = requests.get(
                f"https://api.itch.io/games/{self.game_id}/uploads?download_key_id={self.id}",
                headers={"Authorization": token},
            )
        else:
            response = requests.get(
                f"https://api.itch.io/games/{self.game_id}/uploads",
                headers={"Authorization": token},
            )
        body = response.json()

        if body.get("uploads") is None:
            print(f"> Skipping {self.name}: no uploads")
            return

        for d in body.get("uploads"):
            self.downloads.append(d)

    def download(self, token, platform):
        """Download a singular file"""
        print("Downloading", self.name)

        if self.dir.exists():
            print(f"Skipping Game {self.name}: folder already exists")
            return

        self.load_downloads(token)

        self.dir.mkdir(parents=True, exist_ok=True)

        for d in self.downloads:
            if (
                platform is not None
                and d["traits"]
                and f"p_{platform}" not in d["traits"]
            ):
                print(f"Skipping {self.name} for platform {d['traits']}")
                continue
            self.do_download(d, token)

        with self.dir.with_suffix(".json").open("w") as f:
            json.dump(
                {
                    "name": self.name,
                    "publisher": self.publisher,
                    "link": self.link,
                    "itch_id": self.id,
                    "game_id": self.game_id,
                    "itch_data": self.data,
                },
                f,
                indent=2,
            )

    def do_download(self, d, token):
        """Download a single file, checking for existing files"""
        excluded: list[str] = [
            'Evocación. Cuéntame tu historia.ods',
            'Curious Comrades Vol 1 (version française).zip',
            'Spanish version - Jerséis de Erizo.pdf',
            '2064_08_Linux.zip',
            'SODALITAS-FR-1·5.pdf',
            'SODALITAS-EN-1·5.pdf',
            'Révolution - v0.7.pdf',
            'OldMansJourney-Mac-20180525.zip'
        ]
        if d.get('host') == "thalassa.zeruhur.space" or d.get("filename") in excluded:
            print(f"> Skipping {self.name} - {d.get('id')}")
            return

        print(f"Downloading {d['filename']}")

        filename: str = d.get("filename") or d.get("display_name") or d.get("id")
        out_file = self.dir / filename

        if out_file.exists():
            print(f"File Already Exists! {filename}")
            md5_file = out_file.with_suffix(".md5")
            if md5_file.exists():
                with md5_file.open() as f:
                    md5 = f.read().strip()
                    if md5 == d["md5_hash"]:
                        print(f"Skipping {self.name} - {filename}")
                        return
                    print(f"MD5 Mismatch! {filename}")
            else:
                md5 = utils.md5sum(out_file)
                if md5 == d.get("md5_hash"):
                    print(f"Skipping {self.name} - {filename}")

                    # Create checksum file
                    with md5_file.open("w") as f:
                        f.write(d.get("md5_hash"))
                    return
                # Old Download or corrupted file?
                corrupted = False
                if corrupted:
                    out_file.remove()
                    return

            old_dir = self.dir / "old"
            old_dir.mkdir(exist_ok=True)

            print(f"Moving {filename} to old/")
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d")
            out_file.rename(old_dir / f"{timestamp}-{filename}")

        # Get UUID
        r = requests.post(
            f"https://api.itch.io/games/{self.game_id}/download-sessions",
            headers={"Authorization": token},
        )
        j = r.json()

        # Download
        if self.id:
            url = (
                f"https://api.itch.io/uploads/{d['id']}/"
                + f"download?api_key={token}&download_key_id={self.id}&uuid={j['uuid']}"
            )
        else:
            url = (
                f"https://api.itch.io/uploads/{d['id']}/"
                + f"download?api_key={token}&uuid={j['uuid']}"
            )
        # response_code = urllib.request.urlopen(url).getcode()
        try:
            utils.download(url, self.dir, self.name, filename)
        except utils.NoDownloadError:
            print("Http response is not a download, skipping")

            with open("errors.txt", "a") as f:
                f.write(
                    f""" Cannot download game/asset: {self.game_slug}
                    Publisher Name: {self.publisher_slug}
                    Path: {out_file}
                    File: {filename}
                    Request URL: {url}
                    This request failed due to a missing response header
                    This game/asset has been skipped please download manually
                    ---------------------------------------------------------\n """
                )

            return
        except urllib.error.HTTPError as e:
            print("This one has broken due to an HTTP error!!")

            with open("errors.txt", "a") as f:
                f.write(
                    f""" Cannot download game/asset: {self.game_slug}
                    Publisher Name: {self.publisher_slug}
                    Path: {out_file}
                    File: {filename}
                    Request URL: {url}
                    Request Response Code: {e.code}
                    Error Reason: {e.reason}
                    This game/asset has been skipped please download manually
                    ---------------------------------------------------------\n """
                )

            return

        # Verify
        if utils.md5sum(out_file) != d.get("md5_hash"):
            print(f"Failed to verify {filename}")
            return

        # Create checksum file
        with out_file.with_suffix(".md5").open("w") as f:
            f.write(d["md5_hash"])
