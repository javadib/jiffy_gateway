"""URL configuration for ingestion endpoints."""

from django.urls import path

from apps.ingestion.views import GiteaIngestView, GitHubIngestView, GitLabIngestView

urlpatterns = [
    path("github/ingest", GitHubIngestView.as_view(), name="github-ingest"),
    path("gitlab/ingest", GitLabIngestView.as_view(), name="gitlab-ingest"),
    path("gitea/ingest", GiteaIngestView.as_view(), name="gitea-ingest"),
]
