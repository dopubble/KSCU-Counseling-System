import os

from django import forms


class ConsentUploadForm(forms.Form):
    ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
    MAX_FILE_SIZE = 10 * 1024 * 1024
    ACCEPT_ATTR = ".pdf,.jpg,.jpeg,.png"
    INVALID_TYPE_MESSAGE = "PDF, JPG, PNG만 업로드할 수 있습니다."
    MAX_SIZE_MESSAGE = "파일 크기는 10MB 이하여야 합니다."

    file = forms.FileField(
        label="첨부 파일",
        widget=forms.ClearableFileInput(
            attrs={
                "class": "form-control",
                "accept": ACCEPT_ATTR,
            }
        ),
    )

    def clean_file(self):
        file_obj = self.cleaned_data.get("file")
        if not file_obj:
            return file_obj

        ext = os.path.splitext(file_obj.name)[1].lower()
        if ext not in self.ALLOWED_EXTENSIONS:
            raise forms.ValidationError(self.INVALID_TYPE_MESSAGE)

        if file_obj.size > self.MAX_FILE_SIZE:
            raise forms.ValidationError(self.MAX_SIZE_MESSAGE)

        return file_obj
