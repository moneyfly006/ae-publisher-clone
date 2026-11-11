from aeclone.api import ApiClient, ApiError


def test_client_builds_urls():
    c = ApiClient("https://api.moneyfly.club", "sk-test")
    assert c._url("/v1/models") == "https://api.moneyfly.club/v1/models"


def test_error_extract():
    class R:
        status_code = 400

        def json(self):
            return {"error": {"message": "bad"}}

    assert ApiClient._extract_error(R()) == "bad"
