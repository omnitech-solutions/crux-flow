"""Root URLconf — exercises the recursive resolver with an ``include`` node."""

from django.http import HttpResponse
from django.urls import include, path


def health(request):
    return HttpResponse("ok")


urlpatterns = [
    path("health/", health, name="health"),
    path("blog/", include("runtime_django.blog.urls")),
]
