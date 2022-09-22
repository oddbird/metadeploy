from uuid import uuid4

import pytest

from ..permissions import HasOrgOrReadOnly


class AnonymousUser:
    is_authenticated = False
    is_superuser = False


@pytest.mark.django_db
class TestHasOrgOrReadOnly:
    def test_has_permission__valid_scratch_org(self, rf, scratch_org_factory):
        uuid = str(uuid4())
        permission = HasOrgOrReadOnly()
        request = rf.post("/")
        request.user = AnonymousUser()
        request.session = {"scratch_org_id": uuid}
        scratch_org_factory(uuid=uuid)
        assert permission.has_permission(request, None)

    def test_has_permission__invalid_scratch_org(self, rf):
        uuid = str(uuid4())
        permission = HasOrgOrReadOnly()
        request = rf.post("/")
        request.user = AnonymousUser()
        request.session = {"scratch_org_id": uuid}
        assert not permission.has_permission(request, None)

    def test_has_object_permission__valid_scratch_org(self, rf, scratch_org_factory):
        uuid = str(uuid4())
        permission = HasOrgOrReadOnly()
        request = rf.post("/")
        request.user = AnonymousUser()
        request.session = {"scratch_org_id": uuid}
        org = scratch_org_factory(uuid=uuid, org_id="org_id")
        assert permission.has_object_permission(request, None, org)

    def test_has_object_permission__invalid_scratch_org(self, rf, scratch_org_factory):
        uuid = str(uuid4())
        permission = HasOrgOrReadOnly()
        request = rf.post("/")
        request.user = AnonymousUser()
        request.session = {"scratch_org_id": "other"}
        org = scratch_org_factory(uuid=uuid, org_id="org_id")
        assert not permission.has_object_permission(request, None, org)
