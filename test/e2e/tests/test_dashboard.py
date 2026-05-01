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

"""Integration tests for the QuickSight Dashboard resource.
"""

import pytest
import time
import logging

from acktest.resources import random_suffix_name
from acktest.k8s import resource as k8s
from acktest import tags
from acktest.aws.identity import get_account_id
from e2e import service_marker, CRD_GROUP, CRD_VERSION, load_resource
from e2e.replacement_values import REPLACEMENT_VALUES
from e2e.bootstrap_resources import get_bootstrap_resources

DASHBOARD_RESOURCE_PLURAL = "dashboards"
DATA_SET_RESOURCE_PLURAL = "datasets"
DATA_SOURCE_RESOURCE_PLURAL = "datasources"

MODIFY_WAIT_AFTER_SECONDS = 10
TEMPLATE_WAIT_SECONDS = 15
DASHBOARD_SYNC_WAIT_PERIODS = 4


def _create_data_source(resource_name: str):
    """Helper to create a DataSource CR (dependency for DataSet).
    Returns (ref, cr, aws_account_id).
    """
    aws_account_id = get_account_id()

    bootstrap_resources = get_bootstrap_resources()
    data_source = bootstrap_resources.DataSource
    qs_role = bootstrap_resources.QuickSightS3Role

    if data_source is None or qs_role is None:
        pytest.skip("Bootstrap resources not available. Run service_bootstrap.py first.")

    replacements = REPLACEMENT_VALUES.copy()
    replacements["DATA_SOURCE_NAME"] = resource_name
    replacements["DATA_SOURCE_ID"] = resource_name
    replacements["AWS_ACCOUNT_ID"] = aws_account_id
    replacements["DATA_SOURCE_TYPE"] = "S3"
    replacements["S3_BUCKET_NAME"] = data_source.bucket_name
    replacements["S3_MANIFEST_KEY"] = data_source.manifest_key
    replacements["ROLE_ARN"] = qs_role.arn

    resource_data = load_resource(
        "data_source",
        additional_replacements=replacements,
    )

    ref = k8s.CustomResourceReference(
        CRD_GROUP, CRD_VERSION, DATA_SOURCE_RESOURCE_PLURAL,
        resource_name, namespace="default",
    )
    k8s.create_custom_resource(ref, resource_data)
    cr = k8s.wait_resource_consumed_by_controller(ref)

    assert cr is not None
    assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=10)

    cr = k8s.get_resource(ref)
    return (ref, cr, aws_account_id)


def _create_data_set(resource_name: str, data_source_arn: str, aws_account_id: str):
    """Helper to create a DataSet CR (dependency for Dashboard).
    Returns (ref, cr, data_set_arn).
    """
    replacements = REPLACEMENT_VALUES.copy()
    replacements["DATA_SET_NAME"] = resource_name
    replacements["DATA_SET_ID"] = resource_name
    replacements["AWS_ACCOUNT_ID"] = aws_account_id
    replacements["DATA_SOURCE_ARN"] = data_source_arn

    resource_data = load_resource(
        "data_set",
        additional_replacements=replacements,
    )

    ref = k8s.CustomResourceReference(
        CRD_GROUP, CRD_VERSION, DATA_SET_RESOURCE_PLURAL,
        resource_name, namespace="default",
    )
    k8s.create_custom_resource(ref, resource_data)
    cr = k8s.wait_resource_consumed_by_controller(ref)

    assert cr is not None
    assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=10)

    cr = k8s.get_resource(ref)
    data_set_arn = cr["status"]["ackResourceMetadata"]["arn"]

    return (ref, cr, data_set_arn)


def _create_template(quicksight_client, template_id: str, template_name: str,
                     data_set_arn: str, aws_account_id: str,
                     placeholder: str = "testDataSet"):
    """Helper to create a QuickSight Template via boto3 (not a K8s CR).
    The template is created from the given DataSet using a minimal definition.
    Returns the template ARN.
    """
    quicksight_client.create_template(
        AwsAccountId=aws_account_id,
        TemplateId=template_id,
        Name=template_name,
        Definition={
            "DataSetConfigurations": [
                {
                    "Placeholder": placeholder,
                    "DataSetSchema": {
                        "ColumnSchemaList": [
                            {"Name": "id", "DataType": "STRING"},
                            {"Name": "name", "DataType": "STRING"},
                            {"Name": "value", "DataType": "STRING"},
                            {"Name": "category", "DataType": "STRING"},
                        ],
                    },
                },
            ],
        },
    )

    # Wait for template creation to complete
    for _ in range(10):
        time.sleep(TEMPLATE_WAIT_SECONDS)
        try:
            resp = quicksight_client.describe_template(
                AwsAccountId=aws_account_id,
                TemplateId=template_id,
            )
            status = resp["Template"]["Version"]["Status"]
            if status == "CREATION_SUCCESSFUL":
                return resp["Template"]["Arn"]
            if status in ("CREATION_FAILED", "UPDATE_FAILED"):
                errors = resp["Template"]["Version"].get("Errors", [])
                raise Exception(
                    f"Template creation failed with status {status}: {errors}"
                )
        except quicksight_client.exceptions.ResourceNotFoundException:
            continue

    raise Exception(f"Template {template_id} did not reach CREATION_SUCCESSFUL in time")


def _delete_template(quicksight_client, template_id: str, aws_account_id: str):
    """Helper to delete a QuickSight Template via boto3."""
    try:
        quicksight_client.delete_template(
            AwsAccountId=aws_account_id,
            TemplateId=template_id,
        )
    except Exception:
        logging.warning(f"Failed to delete template {template_id}", exc_info=True)


def _create_dashboard(resource_name: str, template_arn: str, data_set_arn: str,
                      aws_account_id: str):
    """Helper to create a Dashboard CR using a template and return (ref, cr)."""
    replacements = REPLACEMENT_VALUES.copy()
    replacements["DASHBOARD_NAME"] = resource_name
    replacements["DASHBOARD_ID"] = resource_name
    replacements["AWS_ACCOUNT_ID"] = aws_account_id
    replacements["TEMPLATE_ARN"] = template_arn
    replacements["DATA_SET_ARN"] = data_set_arn

    resource_data = load_resource(
        "dashboard",
        additional_replacements=replacements,
    )

    ref = k8s.CustomResourceReference(
        CRD_GROUP, CRD_VERSION, DASHBOARD_RESOURCE_PLURAL,
        resource_name, namespace="default",
    )
    k8s.create_custom_resource(ref, resource_data)
    cr = k8s.wait_resource_consumed_by_controller(ref)

    return (ref, cr)


@pytest.fixture(scope="module")
def dashboard_dependencies(quicksight_client):
    """Creates DataSource, DataSet, and Template as dependencies for Dashboard tests.
    Yields (data_source_ref, data_set_ref, template_arn, aws_account_id, data_set_arn).
    """
    ds_name = random_suffix_name("ack-test-ds-dash", 32)
    (ds_ref, ds_cr, aws_account_id) = _create_data_source(ds_name)
    data_source_arn = ds_cr["status"]["ackResourceMetadata"]["arn"]
    logging.info(f"Created DataSource {ds_name} with ARN {data_source_arn}")

    dset_name = random_suffix_name("ack-test-dset-dash", 32)
    (dset_ref, dset_cr, data_set_arn) = _create_data_set(
        dset_name, data_source_arn, aws_account_id,
    )
    logging.info(f"Created DataSet {dset_name} with ARN {data_set_arn}")

    template_id = random_suffix_name("ack-test-tpl-dash", 32)
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
def simple_dashboard(quicksight_client, dashboard_dependencies):
    """Creates a simple Dashboard for testing using a template source."""
    (_, _, template_arn, aws_account_id, data_set_arn) = dashboard_dependencies
    resource_name = random_suffix_name("ack-test-dash", 24)

    (ref, cr) = _create_dashboard(resource_name, template_arn, data_set_arn, aws_account_id)
    logging.debug(cr)

    assert cr is not None
    assert k8s.get_resource_exists(ref)

    # Dashboard creation is async; give it time to transition from
    # CREATION_IN_PROGRESS to CREATION_SUCCESSFUL before tests start
    time.sleep(MODIFY_WAIT_AFTER_SECONDS)

    yield (ref, cr, aws_account_id)

    # Teardown
    try:
        _, deleted = k8s.delete_custom_resource(ref, 3, 10)
        assert deleted
    except:
        pass


@service_marker
@pytest.mark.canary
class TestDashboard:
    def test_create(self, quicksight_client, simple_dashboard):
        (ref, cr, aws_account_id) = simple_dashboard

        # Wait for the resource to be synced
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=DASHBOARD_SYNC_WAIT_PERIODS)

        # Verify the resource exists in AWS
        dashboard_id = cr["spec"]["id"]

        response = quicksight_client.describe_dashboard(
            AwsAccountId=aws_account_id,
            DashboardId=dashboard_id,
        )

        dash = response["Dashboard"]

        # Verify basic properties
        assert dash["DashboardId"] == dashboard_id
        assert dash["Name"] == cr["spec"]["name"]

        # Verify status fields are populated
        cr = k8s.get_resource(ref)
        assert "status" in cr
        assert "ackResourceMetadata" in cr["status"]
        assert "arn" in cr["status"]["ackResourceMetadata"]

        cr_status = cr["status"]["versionStatus"]
        aws_status = dash["Version"]["Status"]
        assert cr_status == aws_status, (
            f"CR status '{cr_status}' should match AWS status '{aws_status}'"
        )
        assert aws_status in ["CREATION_SUCCESSFUL", "UPDATE_SUCCESSFUL"], (
            f"Expected successful status, got '{aws_status}'"
        )

    def test_update_name(self, quicksight_client, simple_dashboard):
        (ref, cr, aws_account_id) = simple_dashboard

        # Wait for initial sync
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=DASHBOARD_SYNC_WAIT_PERIODS)

        dashboard_id = cr["spec"]["id"]

        # Get initial name
        response = quicksight_client.describe_dashboard(
            AwsAccountId=aws_account_id,
            DashboardId=dashboard_id,
        )
        initial_name = response["Dashboard"]["Name"]

        # Update display name
        new_name = "updated-" + initial_name
        updates = {
            "spec": {
                "name": new_name,
            }
        }

        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)

        # Wait for the update to sync
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=DASHBOARD_SYNC_WAIT_PERIODS)

        # Once synced, the published dashboard should already reflect the new name
        response = quicksight_client.describe_dashboard(
            AwsAccountId=aws_account_id,
            DashboardId=dashboard_id,
        )
        assert response["Dashboard"]["Name"] == new_name
        assert response["Dashboard"]["Name"] != initial_name

        cr = k8s.get_resource(ref)
        cr_status = cr["status"]["versionStatus"]
        aws_status = response["Dashboard"]["Version"]["Status"]
        assert cr_status == aws_status, (
            f"CR status '{cr_status}' should match AWS status '{aws_status}'"
        )
        assert aws_status in ["CREATION_SUCCESSFUL", "UPDATE_SUCCESSFUL"], (
            f"Expected successful status, got '{aws_status}'"
        )

    def test_create_delete_tags(self, quicksight_client, simple_dashboard):
        (ref, cr, aws_account_id) = simple_dashboard

        # Wait for initial sync
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=DASHBOARD_SYNC_WAIT_PERIODS)

        # Get Dashboard ARN
        cr = k8s.get_resource(ref)
        dashboard_arn = cr["status"]["ackResourceMetadata"]["arn"]

        # Test 1: Verify initial tags
        response = quicksight_client.list_tags_for_resource(ResourceArn=dashboard_arn)
        initial_tags = response["Tags"]

        tags.assert_ack_system_tags(tags=initial_tags)

        # Test 2: Add new tag
        updates = {
            "spec": {
                "tags": [
                    {
                        "key": "environment",
                        "value": "test",
                    },
                    {
                        "key": "team",
                        "value": "data-analytics",
                    },
                ]
            }
        }

        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)

        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=DASHBOARD_SYNC_WAIT_PERIODS)

        response = quicksight_client.list_tags_for_resource(ResourceArn=dashboard_arn)
        latest_tags = response["Tags"]
        expected_tags = {"environment": "test", "team": "data-analytics"}

        tags.assert_ack_system_tags(tags=latest_tags)
        tags.assert_equal_without_ack_tags(expected=expected_tags, actual=latest_tags)

        # Test 3: Update tag value
        updates = {
            "spec": {
                "tags": [
                    {
                        "key": "environment",
                        "value": "production",
                    },
                    {
                        "key": "team",
                        "value": "data-analytics",
                    },
                ]
            }
        }

        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)

        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=DASHBOARD_SYNC_WAIT_PERIODS)

        response = quicksight_client.list_tags_for_resource(ResourceArn=dashboard_arn)
        latest_tags = response["Tags"]
        expected_tags = {"environment": "production", "team": "data-analytics"}

        tags.assert_ack_system_tags(tags=latest_tags)
        tags.assert_equal_without_ack_tags(expected=expected_tags, actual=latest_tags)

        # Test 4: Delete all user tags
        updates = {
            "spec": {
                "tags": []
            }
        }

        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)

        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=DASHBOARD_SYNC_WAIT_PERIODS)

        response = quicksight_client.list_tags_for_resource(ResourceArn=dashboard_arn)
        latest_tags = response["Tags"]
        expected_tags = {}

        tags.assert_ack_system_tags(tags=latest_tags)
        tags.assert_equal_without_ack_tags(expected=expected_tags, actual=latest_tags)

    def test_update_source_entity(self, quicksight_client, simple_dashboard, dashboard_dependencies):
        """Test that updating sourceEntity (template ARN, dataset ARN, placeholder)
        triggers a dashboard update and the new values are reflected in AWS."""
        (ref, cr, aws_account_id) = simple_dashboard
        (ds_ref, _, _, _, _) = dashboard_dependencies

        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=DASHBOARD_SYNC_WAIT_PERIODS)

        dashboard_id = cr["spec"]["id"]
        data_source_arn = k8s.get_resource(ds_ref)["status"]["ackResourceMetadata"]["arn"]

        # Record initial source entity ARN from AWS
        resp = quicksight_client.describe_dashboard(
            AwsAccountId=aws_account_id,
            DashboardId=dashboard_id,
        )
        initial_source_arn = resp["Dashboard"]["Version"]["SourceEntityArn"]
        initial_data_set_arns = resp["Dashboard"]["Version"]["DataSetArns"]
        initial_version = resp["Dashboard"]["Version"]["VersionNumber"]
        logging.info(
            f"Initial: sourceEntityArn={initial_source_arn}, "
            f"dataSetArns={initial_data_set_arns}, version={initial_version}"
        )

        # Create a second DataSet and Template to swap to
        dset2_name = random_suffix_name("ack-test-dset2-dash", 32)
        (dset2_ref, _, data_set2_arn) = _create_data_set(
            dset2_name, data_source_arn, aws_account_id,
        )
        logging.info(f"Created second DataSet {dset2_name} with ARN {data_set2_arn}")

        template2_id = random_suffix_name("ack-test-tpl2-dash", 32)
        new_placeholder = "altDataSet"
        template2_arn = _create_template(
            quicksight_client, template2_id, template2_id,
            data_set2_arn, aws_account_id,
            placeholder=new_placeholder,
        )
        logging.info(f"Created second Template {template2_id} with ARN {template2_arn}")

        try:
            # Update the dashboard to use the new template and dataset.
            # The placeholder must match the one defined in the template
            # (created by _create_template with placeholder "testDataSet").
            updates = {
                "spec": {
                    "sourceEntity": {
                        "sourceTemplate": {
                            "arn": template2_arn,
                            "dataSetReferences": [
                                {
                                    "dataSetARN": data_set2_arn,
                                    "dataSetPlaceholder": new_placeholder,
                                },
                            ],
                        },
                    },
                },
            }
            k8s.patch_custom_resource(ref, updates)
            time.sleep(MODIFY_WAIT_AFTER_SECONDS)

            assert k8s.wait_on_condition(
                ref, "ACK.ResourceSynced", "True",
                wait_periods=DASHBOARD_SYNC_WAIT_PERIODS,
            )

            # Verify AWS reflects the new source entity
            resp = quicksight_client.describe_dashboard(
                AwsAccountId=aws_account_id,
                DashboardId=dashboard_id,
            )
            updated_version = resp["Dashboard"]["Version"]
            updated_source_arn = updated_version["SourceEntityArn"]
            updated_data_set_arns = updated_version["DataSetArns"]
            updated_version_number = updated_version["VersionNumber"]

            logging.info(
                f"Updated: sourceEntityArn={updated_source_arn}, "
                f"dataSetArns={updated_data_set_arns}, version={updated_version_number}"
            )

            # Source entity ARN should now point to the new template
            assert template2_arn in updated_source_arn, (
                f"Expected source entity ARN to contain '{template2_arn}', "
                f"got '{updated_source_arn}'"
            )

            # Dataset ARNs should contain the new dataset
            assert data_set2_arn in updated_data_set_arns, (
                f"Expected '{data_set2_arn}' in dataSetArns, "
                f"got {updated_data_set_arns}"
            )

            # Version should have incremented
            assert updated_version_number > initial_version, (
                f"Expected version to increment from {initial_version}, "
                f"got {updated_version_number}"
            )

            # Verify CR status
            cr = k8s.get_resource(ref)
            assert cr["status"]["versionStatus"] in [
                "CREATION_SUCCESSFUL", "UPDATE_SUCCESSFUL",
            ]
        finally:
            # Clean up the second template and dataset
            _delete_template(quicksight_client, template2_id, aws_account_id)
            try:
                _, deleted = k8s.delete_custom_resource(dset2_ref, 3, 10)
                assert deleted
            except:
                pass

    def test_update_permissions(self, quicksight_client, simple_dashboard):
        """Test that granting and revoking dashboard permissions works correctly."""
        (ref, cr, aws_account_id) = simple_dashboard

        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=DASHBOARD_SYNC_WAIT_PERIODS)

        dashboard_id = cr["spec"]["id"]
        # Use the default namespace principal for permissions
        principal_arn = f"arn:aws:quicksight:us-west-2:{aws_account_id}:namespace/default"

        # QuickSight requires dashboard permissions to be granted as a
        # complete predefined set. The valid reader set is:
        reader_actions = [
            "quicksight:DescribeDashboard",
            "quicksight:ListDashboardVersions",
            "quicksight:QueryDashboard",
        ]

        # Step 1: Grant permissions to a principal
        updates = {
            "spec": {
                "permissions": [
                    {
                        "principal": principal_arn,
                        "actions": reader_actions,
                    },
                ],
            },
        }
        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)

        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=DASHBOARD_SYNC_WAIT_PERIODS)

        # Verify permissions in AWS
        resp = quicksight_client.describe_dashboard_permissions(
            AwsAccountId=aws_account_id,
            DashboardId=dashboard_id,
        )
        aws_perms = resp["Permissions"]
        assert len(aws_perms) >= 1
        ns_perm = next((p for p in aws_perms if p["Principal"] == principal_arn), None)
        assert ns_perm is not None, f"Expected permission for {principal_arn}"
        assert set(ns_perm["Actions"]) == set(reader_actions)
        logging.info(f"Granted permissions: {ns_perm}")

        # Step 2: Remove all permissions (revoke the principal)
        updates = {
            "spec": {
                "permissions": [],
            },
        }
        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)

        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=DASHBOARD_SYNC_WAIT_PERIODS)

        resp = quicksight_client.describe_dashboard_permissions(
            AwsAccountId=aws_account_id,
            DashboardId=dashboard_id,
        )
        aws_perms = resp.get("Permissions", [])
        ns_perm = next((p for p in aws_perms if p["Principal"] == principal_arn), None)
        assert ns_perm is None, (
            f"Expected permission for {principal_arn} to be removed, "
            f"but found {ns_perm}"
        )
        logging.info("Permissions removed successfully")

    def test_update_link_entities(self, quicksight_client, simple_dashboard, dashboard_dependencies):
        """Test that updating linkEntities (linked analysis ARNs) works correctly."""
        (ref, cr, aws_account_id) = simple_dashboard
        (_, _, template_arn, _, data_set_arn) = dashboard_dependencies

        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=DASHBOARD_SYNC_WAIT_PERIODS)

        dashboard_id = cr["spec"]["id"]

        # Create an analysis via boto3 to link to the dashboard
        analysis_id = random_suffix_name("ack-test-an-link", 24)
        quicksight_client.create_analysis(
            AwsAccountId=aws_account_id,
            AnalysisId=analysis_id,
            Name=analysis_id,
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
        # Wait for analysis creation
        analysis_arn = None
        for _ in range(10):
            time.sleep(TEMPLATE_WAIT_SECONDS)
            try:
                resp = quicksight_client.describe_analysis(
                    AwsAccountId=aws_account_id,
                    AnalysisId=analysis_id,
                )
                if resp["Analysis"]["Status"] == "CREATION_SUCCESSFUL":
                    analysis_arn = resp["Analysis"]["Arn"]
                    break
            except Exception:
                continue
        assert analysis_arn is not None, f"Analysis {analysis_id} did not reach CREATION_SUCCESSFUL"
        logging.info(f"Created analysis {analysis_id} with ARN {analysis_arn}")

        try:
            # Step 1: Link the analysis to the dashboard
            updates = {
                "spec": {
                    "linkEntities": [analysis_arn],
                },
            }
            k8s.patch_custom_resource(ref, updates)
            time.sleep(MODIFY_WAIT_AFTER_SECONDS)

            assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=DASHBOARD_SYNC_WAIT_PERIODS)

            # Verify in AWS
            resp = quicksight_client.describe_dashboard(
                AwsAccountId=aws_account_id,
                DashboardId=dashboard_id,
            )
            aws_links = resp["Dashboard"].get("LinkEntities", [])
            assert analysis_arn in aws_links, (
                f"Expected {analysis_arn} in LinkEntities, got {aws_links}"
            )
            logging.info(f"Linked analysis: {aws_links}")

            # Step 2: Remove the link
            updates = {
                "spec": {
                    "linkEntities": [],
                },
            }
            k8s.patch_custom_resource(ref, updates)
            time.sleep(MODIFY_WAIT_AFTER_SECONDS)

            assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=DASHBOARD_SYNC_WAIT_PERIODS)

            resp = quicksight_client.describe_dashboard(
                AwsAccountId=aws_account_id,
                DashboardId=dashboard_id,
            )
            aws_links = resp["Dashboard"].get("LinkEntities", [])
            assert analysis_arn not in aws_links, (
                f"Expected {analysis_arn} to be removed from LinkEntities, got {aws_links}"
            )
            logging.info("Link entity removed successfully")
        finally:
            # Clean up the analysis
            try:
                quicksight_client.delete_analysis(
                    AwsAccountId=aws_account_id,
                    AnalysisId=analysis_id,
                    ForceDeleteWithoutRecovery=True,
                )
            except Exception:
                logging.warning(f"Failed to delete analysis {analysis_id}", exc_info=True)

    def test_update_link_sharing_configuration(self, quicksight_client, simple_dashboard):
        """Test that setting and removing linkSharingConfiguration works correctly."""
        (ref, cr, aws_account_id) = simple_dashboard

        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=DASHBOARD_SYNC_WAIT_PERIODS)

        dashboard_id = cr["spec"]["id"]
        principal_arn = f"arn:aws:quicksight:us-west-2:{aws_account_id}:namespace/default"

        link_actions = [
            "quicksight:DescribeDashboard",
            "quicksight:ListDashboardVersions",
            "quicksight:QueryDashboard",
        ]

        # Step 1: Set link sharing configuration
        updates = {
            "spec": {
                "linkSharingConfiguration": {
                    "permissions": [
                        {
                            "principal": principal_arn,
                            "actions": link_actions,
                        },
                    ],
                },
            },
        }
        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)

        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=DASHBOARD_SYNC_WAIT_PERIODS)

        # Verify in AWS
        resp = quicksight_client.describe_dashboard_permissions(
            AwsAccountId=aws_account_id,
            DashboardId=dashboard_id,
        )
        lsc = resp.get("LinkSharingConfiguration")
        assert lsc is not None, "Expected LinkSharingConfiguration to be set"
        lsc_perms = lsc.get("Permissions", [])
        ns_perm = next((p for p in lsc_perms if p["Principal"] == principal_arn), None)
        assert ns_perm is not None, f"Expected link sharing permission for {principal_arn}"
        assert set(ns_perm["Actions"]) == set(link_actions)
        logging.info(f"Link sharing configuration set: {ns_perm}")

        # Step 2: Remove link sharing configuration
        updates = {
            "spec": {
                "linkSharingConfiguration": None,
            },
        }
        k8s.patch_custom_resource(ref, updates)
        time.sleep(MODIFY_WAIT_AFTER_SECONDS)

        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=DASHBOARD_SYNC_WAIT_PERIODS)

        resp = quicksight_client.describe_dashboard_permissions(
            AwsAccountId=aws_account_id,
            DashboardId=dashboard_id,
        )
        lsc = resp.get("LinkSharingConfiguration")
        if lsc is not None:
            lsc_perms = lsc.get("Permissions", [])
            ns_perm = next((p for p in lsc_perms if p["Principal"] == principal_arn), None)
            assert ns_perm is None, (
                f"Expected link sharing permission for {principal_arn} to be removed, "
                f"but found {ns_perm}"
            )
        logging.info("Link sharing configuration removed successfully")

    def test_delete(self, quicksight_client, dashboard_dependencies):
        """Test that deleting the K8s resource deletes the AWS Dashboard."""
        (_, _, template_arn, aws_account_id, data_set_arn) = dashboard_dependencies
        resource_name = random_suffix_name("ack-test-dash-del", 24)

        (ref, cr) = _create_dashboard(resource_name, template_arn, data_set_arn, aws_account_id)

        assert cr is not None
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=DASHBOARD_SYNC_WAIT_PERIODS)

        dashboard_id = cr["spec"]["id"]

        # Verify the Dashboard exists in AWS
        response = quicksight_client.describe_dashboard(
            AwsAccountId=aws_account_id,
            DashboardId=dashboard_id,
        )
        assert response["Dashboard"] is not None

        # Delete the K8s resource
        _, deleted = k8s.delete_custom_resource(ref, 3, 10)
        assert deleted

        # Poll for AWS deletion to complete
        max_wait_periods = 30
        wait_period_length = 5

        for _ in range(max_wait_periods):
            time.sleep(wait_period_length)

            try:
                quicksight_client.describe_dashboard(
                    AwsAccountId=aws_account_id,
                    DashboardId=dashboard_id,
                )
            except quicksight_client.exceptions.ResourceNotFoundException:
                # Successfully deleted
                return

        assert False, (
            f"Dashboard {dashboard_id} was not deleted from AWS "
            f"after {max_wait_periods * wait_period_length} seconds"
        )
