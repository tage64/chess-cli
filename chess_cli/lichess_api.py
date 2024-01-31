from .base import *

from contextlib import suppress
from http.server import HTTPServer, BaseHTTPRequestHandler
import time
from typing import *
from urllib.parse import urljoin, urlsplit
import webbrowser

from authlib.integrations.requests_client import OAuth2Auth, OAuth2Session  # type: ignore
import berserk
import requests

# Uncomment to enable logging of requests:
# import requests
# import logging
# import http.client
#
# logging.basicConfig(level=logging.DEBUG)
# http.client.HTTPConnection.debuglevel = 1
# logging.getLogger().setLevel(logging.DEBUG)
# requests_log = logging.getLogger("requests.packages.urllib3")
# requests_log.setLevel(logging.DEBUG)
# requests_log.propagate = True

LICHESS_HOST: str = "https://lichess.org"
LICHESS_TOKEN_ENDPOINT = urljoin(LICHESS_HOST, "/api/token")
CLIENT_ID: str = "Chess-CLI"
CLIENT_SECRET_KEY: str = "abcdefgoienoienoienoienoienoie"
SCOPE: str = "email:read"


class LichessApi(Base):
    "An extention to chess-cli to connect to the Lichess API."
    _access_token: Optional[str] = None  # Access token to Lichess
    client: Optional[berserk.Client] = None  # Lichess client

    def __init__(self, args: InitArgs) -> None:
        super().__init__(args)
        if self._access_token is not None:
            self.init_client()

    # @override TODO Python 3.12
    def load_config(self) -> None:
        super().load_config()
        try:
            with suppress(KeyError):
                self._access_token = self.config["lichess-api"]["access-token"]
                assert isinstance(self._access_token, str), "Lichess access token must be a str."
        except Exception as ex:
            raise self.config_error(repr(ex))

    # @override TODO Python 3.12
    def save_config(self) -> None:
        self.config["lichess-api"]["access-token"] = self._access_token
        super().save_config()

    authorize_argparser = cmd2.Cmd2ArgumentParser()

    def init_client(self) -> None:
        "Initialize the Lichess client. Assuming that the access token is set."
        assert self._access_token is not None
        session = requests.Session()
        session.headers.update({"Authorization": f"Bearer {self._access_token}"})
        self.client = berserk.Client(session=session)

    @cmd2.with_argparser(authorize_argparser)  # type: ignore
    def do_authorize(self, args) -> None:
        "Authrize Chess-CLI with a Lichess account."
        requestline: Optional[str] = None

        class HTTPRequestHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if not urlsplit(self.path).path == "/authorize":
                    self.send_error(404)
                else:
                    nonlocal requestline
                    requestline = self.requestline
                    self.send_response_only(200)
                    self.wfile.write(
                        b"SUCCESS! Please close this window and return to Chess-CLI.\n"
                    )
                    setattr(self.server, "_BaseServer__shutdown_request", True)

        with HTTPServer(("localhost", 0), HTTPRequestHandler) as httpd:
            client = OAuth2Session(
                CLIENT_ID,
                CLIENT_SECRET_KEY,
                redirect_uri=f"http://{httpd.server_name}:{httpd.server_port}/authorize",
                scope=SCOPE,
                code_challenge_method="S256",
            )
            code_verifier = "srietnrsietniresntiesrntiekgiernsktgiernsktgrnkstgein"
            uri, state = client.create_authorization_url(
                f"{LICHESS_HOST}/oauth", code_verifier=code_verifier
            )
            webbrowser.open(uri)
            self.poutput("If your browser does not open automatically, go to the following URL:")
            self.poutput(uri)
            httpd.serve_forever()
            assert requestline is not None
            token = client.fetch_token(
                LICHESS_TOKEN_ENDPOINT,
                authorization_response=requestline,
                code_verifier=code_verifier,
                client_id=CLIENT_ID,
            )
            self._access_token = token["access_token"]
            self.save_config()
            self.init_client()
