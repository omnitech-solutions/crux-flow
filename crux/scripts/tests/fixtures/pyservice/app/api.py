"""Tiny API layer — imports from .models to give the module graph one edge."""

from .models import Post, User


def list_users():
    return [User, Post]
