import asyncio

from app.services.email_service import send_email


async def main():
    success, error = await send_email(
        to_email="sahaanapushparaj@gmail.com",
        subject="KRATOS Mailjet Test",
        text_body="""
Hello!

This is a test email from the KRATOS backend using Mailjet.

If you received this email, Mailjet is working correctly.

KRATOS'26
""",
        html_body="""
<!DOCTYPE html>
<html>
<body>
    <h2>KRATOS Mailjet Test 🚀</h2>

    <p>Hello!</p>

    <p>
        This is a test email from the KRATOS backend
        using Mailjet.
    </p>

    <p>
        If you received this email, Mailjet is working correctly.
    </p>

    <br>

    <strong>KRATOS'26</strong>
</body>
</html>
""",
    )

    if success:
        print("✅ Mailjet email sent successfully!")
    else:
        print("❌ Mailjet email failed!")
        print("Error:", error)


if __name__ == "__main__":
    asyncio.run(main())