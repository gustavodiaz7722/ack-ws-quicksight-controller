// Copyright Amazon.com Inc. or its affiliates. All Rights Reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License"). You may
// not use this file except in compliance with the License. A copy of the
// License is located at
//
//     http://aws.amazon.com/apache2.0/
//
// or in the "license" file accompanying this file. This file is distributed
// on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
// express or implied. See the License for the specific language governing
// permissions and limitations under the License.

package dashboard

import (
	"context"
	"fmt"
	"strings"

	svcapitypes "github.com/aws-controllers-k8s/quicksight-controller/apis/v1alpha1"
	"github.com/aws-controllers-k8s/quicksight-controller/pkg/sync"
	ackcompare "github.com/aws-controllers-k8s/runtime/pkg/compare"
	"github.com/aws-controllers-k8s/runtime/pkg/metrics"
	ackrequeue "github.com/aws-controllers-k8s/runtime/pkg/requeue"
	ackrtlog "github.com/aws-controllers-k8s/runtime/pkg/runtime/log"
	svcsdk "github.com/aws/aws-sdk-go-v2/service/quicksight"
	svcsdktypes "github.com/aws/aws-sdk-go-v2/service/quicksight/types"
)

var syncTags = sync.Tags
var getTags = sync.GetTags

// dashboardVersionReady calls DescribeDashboard for the given version number
// and returns (true, status) if that version is in a terminal successful state
// and can be published, or (false, status) otherwise.
func dashboardVersionReady(
	ctx context.Context,
	sdkapi *svcsdk.Client,
	m *metrics.Metrics,
	r *resource,
	versionNumber *int64,
) (bool, string) {
	if versionNumber == nil {
		return false, ""
	}
	resp, err := sdkapi.DescribeDashboard(ctx, &svcsdk.DescribeDashboardInput{
		AwsAccountId:  r.ko.Spec.AWSAccountID,
		DashboardId:   r.ko.Spec.ID,
		VersionNumber: versionNumber,
	})
	m.RecordAPICall("READ_ONE", "DescribeDashboard", err)
	if err != nil || resp.Dashboard == nil || resp.Dashboard.Version == nil {
		return false, ""
	}
	status := string(resp.Dashboard.Version.Status)
	ready := resp.Dashboard.Version.Status == svcsdktypes.ResourceStatusCreationSuccessful || resp.Dashboard.Version.Status == svcsdktypes.ResourceStatusUpdateSuccessful
	return ready, status
}

// requeueWaitVersionReady returns a RequeueNeededAfter indicating the
// dashboard version is not yet ready to be published.
func requeueWaitVersionReady(r *resource) *ackrequeue.RequeueNeededAfter {
	status := "unknown"
	if r.ko.Status.VersionStatus != nil {
		status = *r.ko.Status.VersionStatus
	}
	return ackrequeue.NeededAfter(
		fmt.Errorf("dashboard version in '%s' state, waiting to publish", status),
		ackrequeue.DefaultRequeueAfterDuration,
	)
}

// sourceEntityARNsMatch returns true if the desired and latest source entity
// ARNs refer to the same resource. The latest ARN from DescribeDashboard
// includes a /version/N suffix. If the desired ARN is a prefix of the latest
// ARN (i.e. the same base ARN), they match.
func sourceEntityARNsMatch(desired, latest string) bool {
	return strings.HasPrefix(latest, desired)
}

// templateIDFromARN extracts the template ID from a QuickSight template ARN.
// ARN format: arn:aws:quicksight:<region>:<account>:template/<template-id>[/version/<N>]
func templateIDFromARN(arn string) string {
	const prefix = ":template/"
	idx := strings.Index(arn, prefix)
	if idx == -1 {
		return ""
	}
	id := arn[idx+len(prefix):]
	if vIdx := strings.Index(id, "/"); vIdx != -1 {
		id = id[:vIdx]
	}
	return id
}

// resolveDataSetPlaceholders calls DescribeTemplate and returns a map of
// dataset ARN to placeholder name. The DataSetConfigurations in the template
// correspond by position to the DataSetArns on the dashboard version.
func resolveDataSetPlaceholders(
	ctx context.Context,
	sdkapi *svcsdk.Client,
	m *metrics.Metrics,
	awsAccountID *string,
	sourceEntityArn string,
	dataSetArns []string,
) map[string]string {
	result := make(map[string]string, len(dataSetArns))
	templateID := templateIDFromARN(sourceEntityArn)
	if templateID == "" {
		return result
	}
	tplResp, err := sdkapi.DescribeTemplate(ctx, &svcsdk.DescribeTemplateInput{
		AwsAccountId: awsAccountID,
		TemplateId:   &templateID,
	})
	m.RecordAPICall("READ_ONE", "DescribeTemplate", err)
	if err != nil || tplResp.Template == nil || tplResp.Template.Version == nil {
		return result
	}
	configs := tplResp.Template.Version.DataSetConfigurations
	for i, dsARN := range dataSetArns {
		if i < len(configs) && configs[i].Placeholder != nil {
			result[dsARN] = *configs[i].Placeholder
		}
	}
	return result
}

// syncLinkEntities calls UpdateDashboardLinks with the desired list of
// linked analysis ARNs. The API is declarative — it replaces the full list.
func syncLinkEntities(
	ctx context.Context,
	sdkapi *svcsdk.Client,
	m *metrics.Metrics,
	awsAccountID *string,
	dashboardID *string,
	desired []*string,
) error {
	rlog := ackrtlog.FromContext(ctx)
	exit := rlog.Trace("syncLinkEntities")
	defer func() { exit(nil) }()

	linkEntities := make([]string, 0, len(desired))
	for _, e := range desired {
		if e != nil {
			linkEntities = append(linkEntities, *e)
		}
	}
	_, err := sdkapi.UpdateDashboardLinks(ctx, &svcsdk.UpdateDashboardLinksInput{
		AwsAccountId: awsAccountID,
		DashboardId:  dashboardID,
		LinkEntities: linkEntities,
	})
	m.RecordAPICall("UPDATE", "UpdateDashboardLinks", err)
	return err
}

// getDashboardPermissions calls DescribeDashboardPermissions and returns
// the permissions and link sharing configuration.
func getDashboardPermissions(
	ctx context.Context,
	sdkapi *svcsdk.Client,
	m *metrics.Metrics,
	awsAccountID *string,
	dashboardID *string,
) ([]*svcapitypes.ResourcePermission, *svcapitypes.LinkSharingConfiguration, error) {
	resp, err := sdkapi.DescribeDashboardPermissions(ctx, &svcsdk.DescribeDashboardPermissionsInput{
		AwsAccountId: awsAccountID,
		DashboardId:  dashboardID,
	})
	m.RecordAPICall("READ_ONE", "DescribeDashboardPermissions", err)
	if err != nil {
		return nil, nil, err
	}
	perms := convertSDKPermissions(resp.Permissions)
	var lsc *svcapitypes.LinkSharingConfiguration
	if resp.LinkSharingConfiguration != nil && len(resp.LinkSharingConfiguration.Permissions) > 0 {
		lsc = &svcapitypes.LinkSharingConfiguration{
			Permissions: convertSDKPermissions(resp.LinkSharingConfiguration.Permissions),
		}
	}
	return perms, lsc, nil
}

// syncPermissions computes the grant/revoke diff between desired and latest
// permissions and calls UpdateDashboardPermissions.
func syncPermissions(
	ctx context.Context,
	sdkapi *svcsdk.Client,
	m *metrics.Metrics,
	awsAccountID *string,
	dashboardID *string,
	desiredPerms []*svcapitypes.ResourcePermission,
	latestPerms []*svcapitypes.ResourcePermission,
	desiredLSC *svcapitypes.LinkSharingConfiguration,
	latestLSC *svcapitypes.LinkSharingConfiguration,
) error {
	rlog := ackrtlog.FromContext(ctx)
	exit := rlog.Trace("syncPermissions")
	defer func() { exit(nil) }()

	grantPerms, revokePerms := diffPermissions(desiredPerms, latestPerms)

	var desiredLSCPerms, latestLSCPerms []*svcapitypes.ResourcePermission
	if desiredLSC != nil {
		desiredLSCPerms = desiredLSC.Permissions
	}
	if latestLSC != nil {
		latestLSCPerms = latestLSC.Permissions
	}
	grantLinkPerms, revokeLinkPerms := diffPermissions(desiredLSCPerms, latestLSCPerms)

	if len(grantPerms) == 0 && len(revokePerms) == 0 &&
		len(grantLinkPerms) == 0 && len(revokeLinkPerms) == 0 {
		return nil
	}

	input := &svcsdk.UpdateDashboardPermissionsInput{
		AwsAccountId: awsAccountID,
		DashboardId:  dashboardID,
	}
	if len(grantPerms) > 0 {
		input.GrantPermissions = toSDKPermissions(grantPerms)
	}
	if len(revokePerms) > 0 {
		input.RevokePermissions = toSDKPermissions(revokePerms)
	}
	if len(grantLinkPerms) > 0 {
		input.GrantLinkPermissions = toSDKPermissions(grantLinkPerms)
	}
	if len(revokeLinkPerms) > 0 {
		input.RevokeLinkPermissions = toSDKPermissions(revokeLinkPerms)
	}

	_, err := sdkapi.UpdateDashboardPermissions(ctx, input)
	m.RecordAPICall("UPDATE", "UpdateDashboardPermissions", err)
	return err
}

// diffPermissions computes the permissions to grant and revoke to move from
// latest to desired. Each ResourcePermission is keyed by Principal.
//   - Grant: principals in desired but not in latest, or principals whose
//     actions changed.
//   - Revoke: principals in latest but not in desired.
func diffPermissions(
	desired []*svcapitypes.ResourcePermission,
	latest []*svcapitypes.ResourcePermission,
) (grant, revoke []*svcapitypes.ResourcePermission) {
	desiredMap := permissionsByPrincipal(desired)
	latestMap := permissionsByPrincipal(latest)

	// Grant: new or changed principals
	for principal, desiredPerm := range desiredMap {
		latestPerm, exists := latestMap[principal]
		if !exists || !ackcompare.SliceStringPEqual(desiredPerm.Actions, latestPerm.Actions) {
			grant = append(grant, desiredPerm)
		}
	}
	// Revoke: removed principals
	for principal, latestPerm := range latestMap {
		if _, exists := desiredMap[principal]; !exists {
			revoke = append(revoke, latestPerm)
		}
	}
	return grant, revoke
}

// permissionsByPrincipal indexes permissions by principal ARN.
func permissionsByPrincipal(perms []*svcapitypes.ResourcePermission) map[string]*svcapitypes.ResourcePermission {
	m := make(map[string]*svcapitypes.ResourcePermission, len(perms))
	for _, p := range perms {
		if p.Principal != nil {
			m[*p.Principal] = p
		}
	}
	return m
}

// permissionsEqual returns true if two permission slices are equivalent.
func permissionsEqual(a, b []*svcapitypes.ResourcePermission) bool {
	grant, revoke := diffPermissions(a, b)
	return len(grant) == 0 && len(revoke) == 0
}

// convertSDKPermissions converts SDK ResourcePermission slice to CRD type.
func convertSDKPermissions(perms []svcsdktypes.ResourcePermission) []*svcapitypes.ResourcePermission {
	if len(perms) == 0 {
		return nil
	}
	result := make([]*svcapitypes.ResourcePermission, 0, len(perms))
	for _, p := range perms {
		perm := &svcapitypes.ResourcePermission{
			Principal: p.Principal,
		}
		if p.Actions != nil {
			actions := make([]*string, 0, len(p.Actions))
			for i := range p.Actions {
				actions = append(actions, &p.Actions[i])
			}
			perm.Actions = actions
		}
		result = append(result, perm)
	}
	return result
}

// toSDKPermissions converts CRD ResourcePermission slice to SDK type.
func toSDKPermissions(perms []*svcapitypes.ResourcePermission) []svcsdktypes.ResourcePermission {
	result := make([]svcsdktypes.ResourcePermission, 0, len(perms))
	for _, p := range perms {
		sdkPerm := svcsdktypes.ResourcePermission{
			Principal: p.Principal,
		}
		if p.Actions != nil {
			actions := make([]string, 0, len(p.Actions))
			for _, a := range p.Actions {
				if a != nil {
					actions = append(actions, *a)
				}
			}
			sdkPerm.Actions = actions
		}
		result = append(result, sdkPerm)
	}
	return result
}
