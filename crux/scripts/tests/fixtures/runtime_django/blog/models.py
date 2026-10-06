from django.db import models


class Article(models.Model):
    title = models.CharField(max_length=200)
    body = models.TextField()
    published = models.BooleanField(null=True)

    class Meta:
        app_label = "blog"
