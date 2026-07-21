"""URL configuration for ingestion endpoints."""

from django.urls import path

from apps.ingestion.views import GiteaIngestView, GitHubIngestView, GitLabIngestView

urlpatterns = [
    path("ingest/github/", GitHubIngestView.as_view(), name="github-ingest"),
    path("ingest/gitlab/", GitLabIngestView.as_view(), name="gitlab-ingest"),
    path("ingest/gitea/", GiteaIngestView.as_view(), name="gitea-ingest"),
]
