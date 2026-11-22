from django.contrib import admin

from .models import Membership, Store, Tenant, User

admin.site.register(User)
admin.site.register(Tenant)
admin.site.register(Membership)
admin.site.register(Store)
