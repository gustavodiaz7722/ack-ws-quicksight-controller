	if !equality.Semantic.DeepEqual(a.ko.Status.VersionNumber, b.ko.Status.VersionNumber) {
		delta.Add("Status.VersionNumber", a.ko.Status.VersionNumber, b.ko.Status.VersionNumber)
	}
	// Custom SourceEntity comparison. The generated delta skips this field
	// (compare.is_ignored) because DescribeDashboard returns SourceEntityArn
	// with a /version/N suffix.
	if ackcompare.HasNilDifference(a.ko.Spec.SourceEntity, b.ko.Spec.SourceEntity) {
		delta.Add("Spec.SourceEntity", a.ko.Spec.SourceEntity, b.ko.Spec.SourceEntity)
	} else if a.ko.Spec.SourceEntity != nil && b.ko.Spec.SourceEntity != nil {
		if ackcompare.HasNilDifference(a.ko.Spec.SourceEntity.SourceTemplate, b.ko.Spec.SourceEntity.SourceTemplate) {
			delta.Add("Spec.SourceEntity.SourceTemplate", a.ko.Spec.SourceEntity.SourceTemplate, b.ko.Spec.SourceEntity.SourceTemplate)
		} else if a.ko.Spec.SourceEntity.SourceTemplate != nil && b.ko.Spec.SourceEntity.SourceTemplate != nil {
			aARN := a.ko.Spec.SourceEntity.SourceTemplate.ARN
			bARN := b.ko.Spec.SourceEntity.SourceTemplate.ARN
			if ackcompare.HasNilDifference(aARN, bARN) {
				delta.Add("Spec.SourceEntity.SourceTemplate.ARN", aARN, bARN)
			} else if aARN != nil && bARN != nil && !sourceEntityARNsMatch(*aARN, *bARN) {
				delta.Add("Spec.SourceEntity.SourceTemplate.ARN", aARN, bARN)
			}
			if !equality.Semantic.DeepEqual(a.ko.Spec.SourceEntity.SourceTemplate.DataSetReferences, b.ko.Spec.SourceEntity.SourceTemplate.DataSetReferences) {
				delta.Add("Spec.SourceEntity.SourceTemplate.DataSetReferences", a.ko.Spec.SourceEntity.SourceTemplate.DataSetReferences, b.ko.Spec.SourceEntity.SourceTemplate.DataSetReferences)
			}
		}
	}
	// Custom Permissions comparison using principal-keyed diff.
	if !permissionsEqual(a.ko.Spec.Permissions, b.ko.Spec.Permissions) {
		delta.Add("Spec.Permissions", a.ko.Spec.Permissions, b.ko.Spec.Permissions)
	}
	// Custom LinkSharingConfiguration comparison.
	if ackcompare.HasNilDifference(a.ko.Spec.LinkSharingConfiguration, b.ko.Spec.LinkSharingConfiguration) {
		delta.Add("Spec.LinkSharingConfiguration", a.ko.Spec.LinkSharingConfiguration, b.ko.Spec.LinkSharingConfiguration)
	} else if a.ko.Spec.LinkSharingConfiguration != nil && b.ko.Spec.LinkSharingConfiguration != nil {
		if !permissionsEqual(a.ko.Spec.LinkSharingConfiguration.Permissions, b.ko.Spec.LinkSharingConfiguration.Permissions) {
			delta.Add("Spec.LinkSharingConfiguration.Permissions", a.ko.Spec.LinkSharingConfiguration.Permissions, b.ko.Spec.LinkSharingConfiguration.Permissions)
		}
	}
