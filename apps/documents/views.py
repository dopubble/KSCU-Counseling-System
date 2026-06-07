from django.shortcuts import render

from apps.accounts.decorators import role_required
from apps.accounts.models import UserRole


@role_required(UserRole.CLIENT)
def consent_list(request):
    return render(request, "client/consent_list.html")
