from django.shortcuts import render

from apps.accounts.decorators import counselor_required

from .models import CounselingJournal


@counselor_required
def journal_list(request):
    journals = CounselingJournal.objects.filter(counselor=request.user)
    return render(request, "counselor/journal_list.html", {"journals": journals})
