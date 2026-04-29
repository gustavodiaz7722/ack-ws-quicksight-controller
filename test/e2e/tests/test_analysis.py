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

"""Integration tests for the QuickSight Analysis resource.
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

RESOURCE_PLURAL = "analyses"
DATA_SOURCE_RESOURCE_PLURAL = "datasources"
DATA_SET_RESOURCE_PLURAL = "datasets"

MODIFY_WAIT_AFTER_SECONDS = 10
TEMPLATE_WAIT_SECONDS = 15


def _create_data_source(resource_name: str):
    """Helper to create a DataSource CR (dependency for DataSet/Analysis).
    Returns (ref, cr, aws_account_id, data_source_arn).
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
    data_source_arn = cr["status"]["ackResourceMetadata"]["arn"]

    return (ref, cr, aws_account_id, data_source_arn)


def _create_data_set(resource_name: str, data_source_arn: str, aws_account_id: str):
    """Helper to create a DataSet CR (dependency for Analysis).
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
                     data_set_arn: str, aws_account_id: str):
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
                    "Placeholder": "testDataSet",
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


def _create_analysis(resource_name: str, template_arn: str, data_set_arn: str,
                     aws_account_id: str):
    """Helper to create an Analysis CR using a template and return (ref, cr)."""
    replacements = REPLACEMENT_VALUES.copy()
    replacements["ANALYSIS_NAME"] = resource_name
    replacements["ANALYSIS_ID"] = resource_name
    replacements["AWS_ACCOUNT_ID"] = aws_account_id
    replacements["TEMPLATE_ARN"] = template_arn
    replacements["DATA_SET_ARN"] = data_set_arn

    resource_data = load_resource(
        "analysis",
        additional_replacements=replacements,
    )

    ref = k8s.CustomResourceReference(
        CRD_GROUP, CRD_VERSION, RESOURCE_PLURAL,
        resource_name, namespace="default",
    )
    k8s.create_custom_resource(ref, resource_data)
    cr = k8s.wait_resource_consumed_by_controller(ref)

    return (ref, cr)


@pytest.fixture(scope="module")
def data_source_for_analysis(quicksight_client):
    """Creates a DataSource CR to be used as a dependency for Analysis tests."""
    resource_name = random_suffix_name("ack-test-ds-for-an", 24)

    (ref, cr, aws_account_id, data_source_arn) = _create_data_source(resource_name)
    logging.info(f"Created DataSource {resource_name} with ARN {data_source_arn}")

    yield (ref, aws_account_id, data_source_arn)

    # Teardown
    try:
        _, deleted = k8s.delete_custom_resource(ref, 3, 10)
        assert deleted
    except:
        pass


@pytest.fixture(scope="module")
def data_set_for_analysis(quicksight_client, data_source_for_analysis):
    """Creates a DataSet CR to be used as a dependency for Analysis tests."""
    (_, aws_account_id, data_source_arn) = data_source_for_analysis
    resource_name = random_suffix_name("ack-test-dset-for-an", 24)

    (ref, cr, data_set_arn) = _create_data_set(resource_name, data_source_arn, aws_account_id)
    logging.info(f"Created DataSet {resource_name} with ARN {data_set_arn}")

    yield (ref, aws_account_id, data_set_arn)

    # Teardown
    try:
        _, deleted = k8s.delete_custom_resource(ref, 3, 10)
        assert deleted
    except:
        pass


@pytest.fixture(scope="module")
def template_for_analysis(quicksight_client, data_set_for_analysis):
    """Creates a QuickSight Template via boto3 to be used as a source for Analysis tests."""
    (_, aws_account_id, data_set_arn) = data_set_for_analysis
    template_id = random_suffix_name("ack-test-tpl-for-an", 24)
    template_name = template_id

    template_arn = _create_template(
        quicksight_client, template_id, template_name,
        data_set_arn, aws_account_id,
    )
    logging.info(f"Created Template {template_id} with ARN {template_arn}")

    yield (template_arn, aws_account_id, data_set_arn)

    # Teardown
    _delete_template(quicksight_client, template_id, aws_account_id)


@pytest.fixture(scope="module")
def simple_analysis(quicksight_client, template_for_analysis):
    """Creates a simple Analysis for testing using a template source."""
    (template_arn, aws_account_id, data_set_arn) = template_for_analysis
    resource_name = random_suffix_name("ack-test-analysis", 24)

    (ref, cr) = _create_analysis(resource_name, template_arn, data_set_arn, aws_account_id)
    logging.debug(cr)

    assert cr is not None
    assert k8s.get_resource_exists(ref)

    yield (ref, cr, aws_account_id)

    # Teardown: delete analysis first (before dataset/datasource fixtures clean up)
    try:
        _, deleted = k8s.delete_custom_resource(ref, 3, 10)
        assert deleted
    except:
        pass


@service_marker
@pytest.mark.canary
class TestAnalysis:
    def test_create_delete(self, quicksight_client, simple_analysis):
        (ref, cr, aws_account_id) = simple_analysis

        # Wait for the resource to be synced
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=10)

        # Verify the resource exists in AWS
        analysis_id = cr["spec"]["id"]

        response = quicksight_client.describe_analysis(
            AwsAccountId=aws_account_id,
            AnalysisId=analysis_id,
        )

        analysis = response["Analysis"]

        # Verify basic properties
        assert analysis["AnalysisId"] == analysis_id
        assert analysis["Name"] == cr["spec"]["name"]

        # Verify status fields are populated
        cr = k8s.get_resource(ref)
        assert "status" in cr
        assert "ackResourceMetadata" in cr["status"]
        assert "arn" in cr["status"]["ackResourceMetadata"]

        cr_status = cr["status"].get("status")
        aws_status = analysis["Status"]
        assert cr_status == aws_status, (
            f"CR status '{cr_status}' should match AWS status '{aws_status}'"
        )
        assert aws_status in ["CREATION_SUCCESSFUL", "UPDATE_SUCCESSFUL"], (
            f"Expected successful status, got '{aws_status}'"
        )

    def test_update_name(self, quicksight_client, simple_analysis):
        (ref, cr, aws_account_id) = simple_analysis

        # Wait for initial sync
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=10)

        analysis_id = cr["spec"]["id"]

        # Get initial name
        response = quicksight_client.describe_analysis(
            AwsAccountId=aws_account_id,
            AnalysisId=analysis_id,
        )
        initial_name = response["Analysis"]["Name"]

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
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=10)

        # Verify the update in AWS
        response = quicksight_client.describe_analysis(
            AwsAccountId=aws_account_id,
            AnalysisId=analysis_id,
        )

        assert response["Analysis"]["Name"] == new_name
        assert response["Analysis"]["Name"] != initial_name

    def test_create_delete_tags(self, quicksight_client, simple_analysis):
        (ref, cr, aws_account_id) = simple_analysis

        # Wait for initial sync
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=10)

        # Get Analysis ARN
        cr = k8s.get_resource(ref)
        analysis_arn = cr["status"]["ackResourceMetadata"]["arn"]

        # Test 1: Verify initial tags
        response = quicksight_client.list_tags_for_resource(ResourceArn=analysis_arn)
        initial_tags = response["Tags"]

        tags.assert_ack_system_tags(tags=initial_tags)

        # Test 2: Add new tags
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

        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=10)

        response = quicksight_client.list_tags_for_resource(ResourceArn=analysis_arn)
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

        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=10)

        response = quicksight_client.list_tags_for_resource(ResourceArn=analysis_arn)
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

        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=10)

        response = quicksight_client.list_tags_for_resource(ResourceArn=analysis_arn)
        latest_tags = response["Tags"]
        expected_tags = {}

        tags.assert_ack_system_tags(tags=latest_tags)
        tags.assert_equal_without_ack_tags(expected=expected_tags, actual=latest_tags)

    def test_delete(self, quicksight_client, template_for_analysis):
        """Test that deleting the K8s resource deletes the AWS Analysis."""
        (template_arn, aws_account_id, data_set_arn) = template_for_analysis
        resource_name = random_suffix_name("ack-test-an-del", 24)

        (ref, cr) = _create_analysis(resource_name, template_arn, data_set_arn, aws_account_id)

        assert cr is not None
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=10)

        analysis_id = cr["spec"]["id"]

        # Verify the Analysis exists in AWS
        response = quicksight_client.describe_analysis(
            AwsAccountId=aws_account_id,
            AnalysisId=analysis_id,
        )
        assert response["Analysis"] is not None

        # Delete the K8s resource
        _, deleted = k8s.delete_custom_resource(ref, 3, 10)
        assert deleted

        # Poll for AWS deletion to complete
        max_wait_periods = 30
        wait_period_length = 5

        for _ in range(max_wait_periods):
            time.sleep(wait_period_length)

            try:
                resp = quicksight_client.describe_analysis(
                    AwsAccountId=aws_account_id,
                    AnalysisId=analysis_id,
                )
                # QuickSight may return the analysis with DELETED status
                # instead of raising ResourceNotFoundException
                if resp["Analysis"]["Status"] == "DELETED":
                    return
            except quicksight_client.exceptions.ResourceNotFoundException:
                # Successfully deleted
                return

        assert False, (
            f"Analysis {analysis_id} was not deleted from AWS "
            f"after {max_wait_periods * wait_period_length} seconds"
        )
