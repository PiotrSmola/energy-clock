from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("market", "0001_initial"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="pricepoint",
            index=models.Index(fields=["price_pln_mwh"], name="market_price_value_idx"),
        ),
    ]
