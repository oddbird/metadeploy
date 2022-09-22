from ..middleware import GetScratchOrgIdFromQueryStringMiddleware


class MockSession(dict):
    def save(self):
        pass


def test_sets_session(rf):
    request = rf.get("/", {"scratch_org_id": "abc123"})
    request.session = MockSession()
    GetScratchOrgIdFromQueryStringMiddleware(lambda x: x)(request)
    assert "scratch_org_id" in request.session
