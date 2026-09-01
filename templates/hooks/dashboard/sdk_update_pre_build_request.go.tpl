	// UpdateDashboard creates a new version rather than mutating the published
	// one, so promote a version already pending before starting another update,
	// or a second unpublished version is stacked on the first. See
	// shouldPublishPending for why only versions ACK created are promoted.
	pendingVersion := desired.ko.Status.PendingPublishVersionNumber
	if shouldPublishPending(pendingVersion, latest.ko.Status.VersionNumber) {
		ready, versionStatus := dashboardVersionReady(
			ctx, rm.sdkapi, rm.metrics, desired, pendingVersion,
		)
		if !ready {
			return desired, requeueWaitVersionReady(versionStatus)
		}
		_, pubErr := rm.sdkapi.UpdateDashboardPublishedVersion(ctx, &svcsdk.UpdateDashboardPublishedVersionInput{
			AwsAccountId:  desired.ko.Spec.AWSAccountID,
			DashboardId:   desired.ko.Spec.ID,
			VersionNumber: pendingVersion,
		})
		rm.metrics.RecordAPICall("UPDATE", "UpdateDashboardPublishedVersion", pubErr)
		if pubErr != nil {
			// Synced=False is what schedules the retry: the runtime requeues an
			// out-of-sync resource after requeue.DefaultRequeueAfterDuration.
			// Returning the error instead reports Unknown and falls to a backoff
			// that grows past 16 minutes.
			msg := fmt.Sprintf(
				"could not publish dashboard version %d: %s",
				*pendingVersion, pubErr,
			)
			ackcondition.SetSynced(desired, corev1.ConditionFalse, &msg, nil)
			return desired, nil
		}
		// Publishing resolves the spec drift, so stop and let the next reconcile
		// re-read the dashboard.
		desired.ko.Status.VersionNumber = pendingVersion
		desired.ko.Status.VersionStatus = &versionStatus
		desired.ko.Status.PendingPublishVersionNumber = nil
		return desired, nil
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
	if !delta.DifferentExcept("Spec.Tags", "Spec.Permissions", "Spec.LinkSharingConfiguration", "Spec.LinkEntities") {
		return desired, nil
	}

