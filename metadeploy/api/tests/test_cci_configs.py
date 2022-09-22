from unittest import mock

from cumulusci.core.config import UniversalConfig
from cumulusci.utils import temporary_dir, touch

from ..cci_configs import MetadeployProjectConfig


def test_project_config():
    universal_config = UniversalConfig()
    plan = mock.Mock()
    plan.version.product.repo_url = "https://github.com/SFDO-Tooling/CumulusCI-Test"

    with temporary_dir() as path:
        touch("cumulusci.yml")
        project_config = MetadeployProjectConfig(
            universal_config, repo_root=path, plan=plan
        )
        subproject_config = project_config.construct_subproject_config(
            repo_info={
                "root": path,
                "url": "https://github.com/SFDO-Tooling/CumulusCI-Test-Dep",
                "name": "CumulusCI-Test-Dep",
                "owner": "SFDO-Tooling",
                "commit": "abcdef",
            }
        )

    assert subproject_config.plan is project_config.plan is plan
