"""URL configuration for ingestion endpoints."""

from django.urls import path

from apps.ingestion.views import GiteaIngestView, GitHubIngestView, GitLabIngestView

urlpatterns = [
    path("github/ingestion", GitHubIngestView.as_view(), name="github-ingestion"),
    path("gitlab/ingestion", GitLabIngestView.as_view(), name="gitlab-ingestion"),
    path("gitea/ingestion", GiteaIngestView.as_view(), name="gitea-ingestion"),
]
