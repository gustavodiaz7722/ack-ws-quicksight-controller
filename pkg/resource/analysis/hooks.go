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

package analysis

import (
	"github.com/aws-controllers-k8s/quicksight-controller/pkg/sync"
	svcsdktypes "github.com/aws/aws-sdk-go-v2/service/quicksight/types"
)

var syncTags = sync.Tags
var getTags = sync.GetTags

func analysisIsCreationSuccessful(desired *resource) bool {
	if desired.ko.Status.Status != nil && *desired.ko.Status.Status == string(svcsdktypes.ResourceStatusCreationSuccessful) {
		return true
	}

	return false
}

func analysisIsUpdateSuccessful(desired *resource) bool {
	if desired.ko.Status.Status != nil && *desired.ko.Status.Status == string(svcsdktypes.ResourceStatusUpdateSuccessful) {
		return true
	}

	return false
}

func analysisIsCreationFailed(desired *resource) bool {
	if desired.ko.Status.Status != nil && *desired.ko.Status.Status == string(svcsdktypes.ResourceStatusCreationFailed) {
		return true
	}

	return false
}

func analysisIsUpdateFailed(desired *resource) bool {
	if desired.ko.Status.Status != nil && *desired.ko.Status.Status == string(svcsdktypes.ResourceStatusUpdateFailed) {
		return true
	}

	return false
}

func isAnalysisUpdateReady(desired *resource) bool {
	return !(analysisIsCreationSuccessful(desired) || analysisIsUpdateSuccessful(desired) || analysisIsUpdateFailed(desired) || analysisIsCreationFailed(desired))
}
