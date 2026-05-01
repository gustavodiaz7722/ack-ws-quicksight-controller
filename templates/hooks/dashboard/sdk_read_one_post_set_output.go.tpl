	if resp.Dashboard.Version != nil {
		if resp.Dashboard.Version.VersionNumber != nil {
			ko.Status.VersionNumber = resp.Dashboard.Version.VersionNumber
		}
		if resp.Dashboard.Version.Status != "" {
			ko.Status.VersionStatus = aws.String(string(resp.Dashboard.Version.Status))
		}
		if resp.Dashboard.Version.ThemeArn != nil {
			ko.Spec.ThemeARN = resp.Dashboard.Version.ThemeArn
		}
		if resp.Dashboard.Version.Description != nil {
			ko.Spec.VersionDescription = resp.Dashboard.Version.Description
		}
		if resp.Dashboard.Version.SourceEntityArn != nil {
			placeholderByARN := resolveDataSetPlaceholders(
				ctx, rm.sdkapi, rm.metrics,
				ko.Spec.AWSAccountID,
				*resp.Dashboard.Version.SourceEntityArn,
				resp.Dashboard.Version.DataSetArns,
			)
			var dataSetRefs []*svcapitypes.DataSetReference
			for _, dsARN := range resp.Dashboard.Version.DataSetArns {
				ref := &svcapitypes.DataSetReference{
					DataSetARN: aws.String(dsARN),
				}
				if placeholder, ok := placeholderByARN[dsARN]; ok {
					ref.DataSetPlaceholder = aws.String(placeholder)
				}
				dataSetRefs = append(dataSetRefs, ref)
			}
			ko.Spec.SourceEntity = &svcapitypes.DashboardSourceEntity{
				SourceTemplate: &svcapitypes.DashboardSourceTemplate{
					ARN:               resp.Dashboard.Version.SourceEntityArn,
					DataSetReferences: dataSetRefs,
				},
			}
		}
	}
	// Fetch permissions and link sharing configuration
	perms, lsc, permErr := getDashboardPermissions(
		ctx, rm.sdkapi, rm.metrics,
		ko.Spec.AWSAccountID, ko.Spec.ID,
	)
	if permErr == nil {
		ko.Spec.Permissions = perms
		ko.Spec.LinkSharingConfiguration = lsc
	}
	ko.Spec.Tags, err = getTags(ctx, string(*ko.Status.ACKResourceMetadata.ARN), rm.sdkapi, rm.metrics)
	if err != nil {
		return &resource{ko}, err
	}
