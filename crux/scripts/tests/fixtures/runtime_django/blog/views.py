from django.http import HttpResponse


def article_list(request):
    return HttpResponse("articles")


def article_detail(request, pk):
    return HttpResponse(f"article {pk}")
