# Data model

_Derived from Django ORM models._

## Django models (7)

| table | column | type | nullable | pk | fk |
|---|---|---|---|---|---|
| base_footertext | `body` | RichTextField | no | no | — |
| blog_blogpersonrelationship | `person` | ForeignKey | no | no | base.Person |
| breads_breadingredient | `name` | CharField | no | no | — |
| breads_breadtype | `title` | CharField | no | no | — |
| breads_country | `sort_order` | IntegerField | yes | no | — |
| breads_country | `title` | CharField | no | no | — |
| locations_locationoperatinghours | `closed` | BooleanField | no | no | — |
| locations_locationoperatinghours | `closing_time` | TimeField | yes | no | — |
| locations_locationoperatinghours | `day` | CharField | no | no | — |
| locations_locationoperatinghours | `opening_time` | TimeField | yes | no | — |
| recipes_recipepersonrelationship | `person` | ForeignKey | no | no | base.Person |

## Residuals

- Django adds an implicit `id` primary key to a model that declares none, and the ORM adds it rather than the model, so no row here reports one unless the model declared `primary_key=True` itself.
- Field inheritance is resolved WITHIN one module. A model whose base class is imported from another module renders only the fields it declares itself, because resolving the base would mean deciding which of several same-named classes across the repository the import meant.
