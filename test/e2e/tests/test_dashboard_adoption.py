# Copyright Amazon.com Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may
# not use this file except in compliance with the License. A copy of the
# License is located at
#
# 	 http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is distributed
# on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
# express or implied. See the License for the specific language governing
# permissions and limitations under the License.

"""Integration tests for QuickSight Dashboard adoption behavior.

Validates that when a dashboard with multiple published revisions is adopted
by ACK, the controller preserves the current published version and does not
publish a newer draft version. Also validates that updating an adopted
dashboard creates and publishes a new version.
"""

import pytest
import time
import logging

from acktest.resources import random_suffix_name
from acktest.k8s import resource as k8s
from acktest.aws.identity import get_account_id
from e2e import service_marker, CRD_GROUP, CRD_VERSION, load_resource
from e2e.replacement_values import REPLACEMENT_VALUES
from e2e.bootstrap_resources import get_bootstrap_resources
from e2e.tests.test_dashboard import (
    _create_data_source,
    _create_data_set,
    _create_template,
    _delete_template,
    DASHBOARD_RESOURCE_PLURAL,
    MODIFY_WAIT_AFTER_SECONDS,
    DASHBOARD_SYNC_WAIT_PERIODS,
)

DASHBOARD_CREATION_WAIT_SECONDS = 30


def _wait_dashboard_version_status(
    quicksight_client, aws_account_id, dashboard_id, version_number, target_statuses,
    max_attempts=20, wait_seconds=5,
):
    """Poll DescribeDashboard for a specific version until it reaches one of
    the target statuses. Returns the version status string.
    """
    for _ in range(max_attempts):
        resp = quicksight_client.describe_dashboard(
            AwsAccountId=aws_account_id,
            DashboardId=dashboard_id,
            VersionNumber=version_number,
        )
        status = resp["Dashboard"]["Version"]["Status"]
        if status in target_statuses:
            return status
        time.sleep(wait_seconds)
    raise Exception(
        f"Dashboard {dashboard_id} version {version_number} did not reach "
        f"{target_statuses} in time; last status: {status}"
    )


def _create_dashboard_via_boto(
    quicksight_client, dashboard_id, dashboard_name, template_arn,
    data_set_arn, aws_account_id,
):
    """Create a dashboard directly via boto3 (not through ACK).
    Returns the dashboard ARN.
    """
    resp = quicksight_client.create_dashboard(
        AwsAccountId=aws_account_id,
        DashboardId=dashboard_id,
        Name=dashboard_name,
        SourceEntity={
            "SourceTemplate": {
                "Arn": template_arn,
                "DataSetReferences": [
                    {
                        "DataSetArn": data_set_arn,
                        "DataSetPlaceholder": "testDataSet",
                    },
                ],
            },
        },
    )
    return resp["Arn"]


def _update_dashboard_via_boto(
    quicksight_client, dashboard_id, dashboard_name, template_arn,
    data_set_arn, aws_account_id,
):
    """Update a dashboard directly via boto3 to create a new draft version.
    Returns the version ARN.
    """
    resp = quicksight_client.update_dashboard(
        AwsAccountId=aws_account_id,
        DashboardId=dashboard_id,
        Name=dashboard_name,
        SourceEntity={
            "SourceTemplate": {
                "Arn": template_arn,
                "DataSetReferences": [
                    {
                        "DataSetArn": data_set_arn,
                        "DataSetPlaceholder": "testDataSet",
                    },
                ],
            },
        },
    )
    return resp.get("VersionArn")


def _delete_dashboard_via_boto(quicksight_client, dashboard_id, aws_account_id):
    """Delete a dashboard directly via boto3."""
    try:
        quicksight_client.delete_dashboard(
            AwsAccountId=aws_account_id,
            DashboardId=dashboard_id,
        )
    except Exception:
        logging.warning(f"Failed to delete dashboard {dashboard_id}", exc_info=True)


@pytest.fixture(scope="module")
def adoption_dependencies(quicksight_client):
    """Creates DataSource, DataSet, and Template as dependencies for adoption tests.
    Yields (data_source_ref, data_set_ref, template_arn, aws_account_id, data_set_arn).
    """
    ds_name = random_suffix_name("ack-test-ds-adopt", 32)
    (ds_ref, ds_cr, aws_account_id) = _create_data_source(ds_name)
    data_source_arn = ds_cr["status"]["ackResourceMetadata"]["arn"]
    logging.info(f"Created DataSource {ds_name} with ARN {data_source_arn}")

    dset_name = random_suffix_name("ack-test-dset-adopt", 32)
    (dset_ref, dset_cr, data_set_arn) = _create_data_set(
        dset_name, data_source_arn, aws_account_id,
    )
    logging.info(f"Created DataSet {dset_name} with ARN {data_set_arn}")

    template_id = random_suffix_name("ack-test-tpl-adopt", 32)
    template_arn = _create_template(
        quicksight_client, template_id, template_id,
        data_set_arn, aws_account_id,
    )
    logging.info(f"Created Template {template_id} with ARN {template_arn}")

    yield (ds_ref, dset_ref, template_arn, aws_account_id, data_set_arn)

    # Teardown: delete in reverse dependency order
    _delete_template(quicksight_client, template_id, aws_account_id)
    try:
        _, deleted = k8s.delete_custom_resource(dset_ref, 3, 10)
        assert deleted
    except:
        pass
    time.sleep(MODIFY_WAIT_AFTER_SECONDS)
    try:
        _, deleted = k8s.delete_custom_resource(ds_ref, 3, 10)
        assert deleted
    except:
        pass


@pytest.fixture(scope="module")
def boto_dashboard_with_draft(quicksight_client, adoption_dependencies):
    """Creates a dashboard via boto3 with version 1 published and version 2
    as an unpublished draft.

    Yields (dashboard_id, aws_account_id, template_arn, data_set_arn).
    """
    (_, _, template_arn, aws_account_id, data_set_arn) = adoption_dependencies
    dashboard_id = random_suffix_name("ack-adopt-dash", 24)
    dashboard_name = dashboard_id

    # Create dashboard (version 1, auto-published)
    _create_dashboard_via_boto(
        quicksight_client, dashboard_id, dashboard_name,
        template_arn, data_set_arn, aws_account_id,
    )
    logging.info(f"Created dashboard {dashboard_id} via boto3")

    # Wait for version 1 to be ready
    _wait_dashboard_version_status(
        quicksight_client, aws_account_id, dashboard_id,
        version_number=1,
        target_statuses=["CREATION_SUCCESSFUL"],
    )
    logging.info(f"Dashboard {dashboard_id} version 1 is CREATION_SUCCESSFUL")

    # Update dashboard to create version 2 (draft, not published)
    _update_dashboard_via_boto(
        quicksight_client, dashboard_id, f"{dashboard_name}-v2",
        template_arn, data_set_arn, aws_account_id,
    )
    logging.info(f"Updated dashboard {dashboard_id} to create version 2 (draft)")

    # Wait for version 2 to be ready
    _wait_dashboard_version_status(
        quicksight_client, aws_account_id, dashboard_id,
        version_number=2,
        target_statuses=["CREATION_SUCCESSFUL"],
    )
    logging.info(f"Dashboard {dashboard_id} version 2 is CREATION_SUCCESSFUL (draft)")

    yield (dashboard_id, aws_account_id, template_arn, data_set_arn)

    # Teardown: delete the AWS dashboard
    _delete_dashboard_via_boto(quicksight_client, dashboard_id, aws_account_id)


@pytest.fixture()
def adopted_dashboard(boto_dashboard_with_draft):
    """Adopts the boto-created dashboard into ACK and yields (ref, cr, dashboard_id, aws_account_id).
    Cleans up the K8s CR on teardown (deletion-policy: retain keeps the AWS resource).
    """
    (dashboard_id, aws_account_id, _, _) = boto_dashboard_with_draft

    resource_name = random_suffix_name("ack-adopt-dash-cr", 24)
    replacements = REPLACEMENT_VALUES.copy()
    replacements["DASHBOARD_NAME"] = resource_name
    replacements["DASHBOARD_ID"] = dashboard_id
    replacements["AWS_ACCOUNT_ID"] = aws_account_id

    resource_data = load_resource(
        "dashboard_adoption",
        additional_replacements=replacements,
    )

    ref = k8s.CustomResourceReference(
        CRD_GROUP, CRD_VERSION, DASHBOARD_RESOURCE_PLURAL,
        resource_name, namespace="default",
    )
    k8s.create_custom_resource(ref, resource_data)
    cr = k8s.wait_resource_consumed_by_controller(ref)
    assert cr is not None

    assert k8s.wait_on_condition(
        ref, "ACK.ResourceSynced", "True",
        wait_periods=DASHBOARD_SYNC_WAIT_PERIODS,
    )

    cr = k8s.get_resource(ref)

    yield (ref, cr, dashboard_id, aws_account_id)

    # Teardown: delete the K8s CR (deletion-policy: retain keeps the AWS resource)
    try:
        _, deleted = k8s.delete_custom_resource(ref, 3, 10)
        assert deleted
    except:
        pass


@service_marker
@pytest.mark.canary
class TestDashboardAdoption:
    def test_adopt_preserves_published_version(
        self, quicksight_client, adopted_dashboard,
    ):
        """When a dashboard has multiple versions but is published on an older
        revision, adopting it into ACK should preserve the published version's
        versionNumber and versionStatus. The controller must not publish the
        newer draft version.
        """
        (ref, cr, dashboard_id, aws_account_id) = adopted_dashboard

        assert "status" in cr

        cr_version_number = cr["status"].get("versionNumber")
        cr_version_status = cr["status"].get("versionStatus")

        logging.info(
            f"Adopted dashboard CR: versionNumber={cr_version_number}, "
            f"versionStatus={cr_version_status}"
        )

        # The adopted dashboard should reflect the published version (1),
        # not the latest draft version (2)
        assert cr_version_number == 1, (
            f"Expected adopted versionNumber to be 1 (published), "
            f"got {cr_version_number}"
        )
        assert cr_version_status == "CREATION_SUCCESSFUL", (
            f"Expected adopted versionStatus to be CREATION_SUCCESSFUL, "
            f"got {cr_version_status}"
        )

        # Verify AWS still has version 1 as the published version
        # (controller did not publish version 2)
        resp = quicksight_client.describe_dashboard(
            AwsAccountId=aws_account_id,
            DashboardId=dashboard_id,
        )
        aws_published_version = resp["Dashboard"]["Version"]["VersionNumber"]
        assert aws_published_version == 1, (
            f"Expected AWS published version to remain 1 after adoption, "
            f"got {aws_published_version}"
        )

    def test_update_adopted_dashboard_publishes_new_version(
        self, quicksight_client, adopted_dashboard, adoption_dependencies,
    ):
        """After adopting a dashboard, updating its name via ACK should create
        a new version and publish it. The versionNumber should increment and
        versionStatus should reach a successful state. The draft version (2)
        must remain unpublished, and only the latest version should be published.
        """
        (ref, cr, dashboard_id, aws_account_id) = adopted_dashboard
        (_, _, template_arn, _, data_set_arn) = adoption_dependencies

        # Record the version number before update
        initial_version = cr["status"].get("versionNumber")
        initial_name = cr["spec"]["name"]
        logging.info(
            f"Before update: versionNumber={initial_version}, name={initial_name}"
        )

        # Verify the draft version (2) is NOT published before the update.
        # DescribeDashboard without VersionNumber returns the published version;
        # it should still be version 1, not the draft version 2.
        resp_before = quicksight_client.describe_dashboard(
            AwsAccountId=aws_account_id,
            DashboardId=dashboard_id,
        )
        published_before = resp_before["Dashboard"]["Version"]["VersionNumber"]
        assert published_before == 1, (
            f"Expected published version to be 1 (not draft version 2) before "
            f"update, got {published_before}"
        )

        # Also confirm version 2 exists but is not the published version
        resp_v2 = quicksight_client.describe_dashboard(
            AwsAccountId=aws_account_id,
            DashboardId=dashboard_id,
            VersionNumber=2,
        )
        v2_status = resp_v2["Dashboard"]["Version"]["Status"]
        logging.info(f"Draft version 2 status: {v2_status}")
        assert v2_status == "CREATION_SUCCESSFUL", (
            f"Expected draft version 2 to have status CREATION_SUCCESSFUL, "
            f"got {v2_status}"
        )
        # Draft version 2 should not be the published version
        assert published_before != 2, (
            "Draft version 2 should not be the published version before update"
        )

        # Update the dashboard name
        new_name = f"updated-{initial_name}"
        updates = {
            "spec": {
                "name": new_name,
            },
        }
        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)

        # Wait for the update to sync
        assert k8s.wait_on_condition(
            ref, "ACK.ResourceSynced", "True",
            wait_periods=DASHBOARD_SYNC_WAIT_PERIODS,
        )

        cr = k8s.get_resource(ref)
        updated_version = cr["status"].get("versionNumber")
        updated_status = cr["status"].get("versionStatus")

        logging.info(
            f"After update: versionNumber={updated_version}, "
            f"versionStatus={updated_status}"
        )

        # Version number should have incremented
        assert updated_version is not None
        assert updated_version > initial_version, (
            f"Expected versionNumber to increment from {initial_version}, "
            f"got {updated_version}"
        )

        # Version status should be successful
        assert updated_status in ["CREATION_SUCCESSFUL", "UPDATE_SUCCESSFUL"], (
            f"Expected successful versionStatus, got {updated_status}"
        )

        # Verify the latest version IS published by checking the default
        # (published) view matches the updated version number and name
        resp_after = quicksight_client.describe_dashboard(
            AwsAccountId=aws_account_id,
            DashboardId=dashboard_id,
        )
        aws_published_version = resp_after["Dashboard"]["Version"]["VersionNumber"]
        aws_name = resp_after["Dashboard"]["Name"]

        assert aws_published_version == updated_version, (
            f"Expected latest version {updated_version} to be published, "
            f"but published version is {aws_published_version}"
        )
        assert aws_name == new_name, (
            f"Expected AWS dashboard name '{new_name}', got '{aws_name}'"
        )

        # Verify the draft version (2) is still NOT the published version
        assert aws_published_version != 2, (
            f"Draft version 2 should not be the published version after update, "
            f"but published version is {aws_published_version}"
        )

        # The new published version should be exactly 1 more than the draft
        # version (2), i.e. version 3
        assert updated_version == 3, (
            f"Expected new version to be 3 (draft version 2 + 1), "
            f"got {updated_version}"
        )

        # Confirm the specific updated version is accessible and has the
        # correct status, proving it was actually published
        resp_latest = quicksight_client.describe_dashboard(
            AwsAccountId=aws_account_id,
            DashboardId=dashboard_id,
            VersionNumber=updated_version,
        )
        latest_version_status = resp_latest["Dashboard"]["Version"]["Status"]
        assert latest_version_status in ["CREATION_SUCCESSFUL", "UPDATE_SUCCESSFUL"], (
            f"Expected published version {updated_version} to have successful "
            f"status, got {latest_version_status}"
        )
