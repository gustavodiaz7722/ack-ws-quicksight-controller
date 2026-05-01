	// A VersionNumber diff means UpdateDashboard created a new draft version.
	// We must publish it before updating Status.VersionNumber on the desired
	// resource. If we set VersionNumber before a successful publish, a
	// subsequent reconcile would see no diff and skip the publish, leaving
	// the dashboard stuck on the old published version.
	if delta.DifferentAt("Status.VersionNumber") {
		ready, versionStatus := dashboardVersionReady(ctx, rm.sdkapi, rm.metrics, desired, desired.ko.Status.VersionNumber)
		if !ready {
			return desired, requeueWaitVersionReady(desired)
		}
		_, pubErr := rm.sdkapi.UpdateDashboardPublishedVersion(ctx, &svcsdk.UpdateDashboardPublishedVersionInput{
			AwsAccountId:  desired.ko.Spec.AWSAccountID,
			DashboardId:   desired.ko.Spec.ID,
			VersionNumber: desired.ko.Status.VersionNumber,
		})
		rm.metrics.RecordAPICall("UPDATE", "UpdateDashboardPublishedVersion", pubErr)
		if pubErr != nil {
			return desired, pubErr
		}
		// VersionNumber is already correct on desired. Set VersionStatus
		// from the DescribeDashboard result for the desired version.
		desired.ko.Status.VersionStatus = &versionStatus
	}
	if delta.DifferentAt("Spec.Tags") {
		arn := string(*latest.ko.Status.ACKResourceMetadata.ARN)
		err = syncTags(
			ctx,
			desired.ko.Spec.Tags, latest.ko.Spec.Tags,
			&arn, convertToOrderedACKTags, rm.sdkapi, rm.metrics,
		)
		if err != nil {
			return desired, err
		}
	}
	if delta.DifferentAt("Spec.Permissions") || delta.DifferentAt("Spec.LinkSharingConfiguration") {
		err = syncPermissions(
			ctx, rm.sdkapi, rm.metrics,
			desired.ko.Spec.AWSAccountID,
			desired.ko.Spec.ID,
			desired.ko.Spec.Permissions,
			latest.ko.Spec.Permissions,
			desired.ko.Spec.LinkSharingConfiguration,
			latest.ko.Spec.LinkSharingConfiguration,
		)
		if err != nil {
			return desired, err
		}
	}
	if delta.DifferentAt("Spec.LinkEntities") {
		err = syncLinkEntities(
			ctx, rm.sdkapi, rm.metrics,
			desired.ko.Spec.AWSAccountID,
			desired.ko.Spec.ID,
			desired.ko.Spec.LinkEntities,
		)
		if err != nil {
			return desired, err
		}
	}
	if !delta.DifferentExcept("Spec.Tags", "Spec.Permissions", "Spec.LinkSharingConfiguration", "Spec.LinkEntities", "Status.VersionNumber") {
		return desired, nil
	}

