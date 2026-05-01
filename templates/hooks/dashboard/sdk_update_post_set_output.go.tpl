	if resp.CreationStatus != "" {
		ko.Status.VersionStatus = aws.String(string(resp.CreationStatus))
	}
	if resp.VersionArn != nil {
		// VersionArn format: arn:aws:quicksight:<region>:<account>:dashboard/<id>/version/<number>
		parts := strings.Split(*resp.VersionArn, "/version/")
		if len(parts) == 2 {
			var vn int64
			if _, scanErr := fmt.Sscanf(parts[1], "%d", &vn); scanErr == nil {
				ko.Status.VersionNumber = &vn
			}
		}
	}
