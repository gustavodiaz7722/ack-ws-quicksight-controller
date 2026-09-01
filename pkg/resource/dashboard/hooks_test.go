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
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/stretchr/testify/assert"
)

func TestShouldPublishPending(t *testing.T) {
	tests := []struct {
		name      string
		pending   *int64
		published *int64
		want      bool
	}{
		{
			// The adoption case: AWS holds a draft created outside ACK, which
			// must be left alone.
			name:      "no pending version recorded",
			pending:   nil,
			published: aws.Int64(1),
			want:      false,
		},
		{
			name:      "pending is newer than published",
			pending:   aws.Int64(2),
			published: aws.Int64(1),
			want:      true,
		},
		{
			name:      "pending already published",
			pending:   aws.Int64(2),
			published: aws.Int64(2),
			want:      false,
		},
		{
			// A stale marker, e.g. published out of band. Must not move the
			// dashboard backwards.
			name:      "pending is older than published",
			pending:   aws.Int64(1),
			published: aws.Int64(2),
			want:      false,
		},
		{
			name:      "published unknown",
			pending:   aws.Int64(2),
			published: nil,
			want:      true,
		},
		{
			name:      "neither known",
			pending:   nil,
			published: nil,
			want:      false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			assert.Equal(t, tt.want, shouldPublishPending(tt.pending, tt.published))
		})
	}
}
