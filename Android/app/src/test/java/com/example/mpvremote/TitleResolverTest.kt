package com.example.mpvremote

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TitleResolverTest {

    @Test
    fun `isYouTubeUrl detects youtube dot com watch`() {
        assertTrue(TitleResolver.isYouTubeUrl("https://www.youtube.com/watch?v=12345"))
    }

    @Test
    fun `isYouTubeUrl detects youtu dot be`() {
        assertTrue(TitleResolver.isYouTubeUrl("https://youtu.be/abcdef"))
    }

    @Test
    fun `isYouTubeUrl detects shorts`() {
        assertTrue(TitleResolver.isYouTubeUrl("https://youtube.com/shorts/xyz"))
    }

    @Test
    fun `isYouTubeUrl detects embed`() {
        assertTrue(TitleResolver.isYouTubeUrl("https://www.youtube.com/embed/abc"))
    }

    @Test
    fun `isYouTubeUrl detects mobile youtube`() {
        assertTrue(TitleResolver.isYouTubeUrl("https://m.youtube.com/watch?v=123"))
    }

    @Test
    fun `isYouTubeUrl rejects non youtube`() {
        assertFalse(TitleResolver.isYouTubeUrl("https://twitch.tv/videos/123"))
    }

    @Test
    fun `buildYouTubeOEmbedUrl returns encoded url`() {
        val result = TitleResolver.buildYouTubeOEmbedUrl("https://www.youtube.com/watch?v=abc&list=xyz")
        assertTrue(result!!.startsWith("https://www.youtube.com/oembed?format=json&url="))
        assertTrue(result.contains("watch%3Fv%3Dabc"))
    }

    @Test
    fun `buildYouTubeOEmbedUrl returns null for blank url`() {
        assertNull(TitleResolver.buildYouTubeOEmbedUrl("   "))
    }

    @Test
    fun `extractTitle prefers og title`() {
        val html = """
            <head>
                <meta property="og:title" content="OpenGraph Title" />
                <meta name="twitter:title" content="Twitter Title" />
                <title>Tag Title</title>
            </head>
        """.trimIndent()
        assertEquals("OpenGraph Title", TitleResolver.extractTitle(html, "example.com"))
    }

    @Test
    fun `extractTitle falls back to twitter title`() {
        val html = """
            <head>
                <meta name="twitter:title" content='Twitter Title' />
                <title>Tag Title</title>
            </head>
        """.trimIndent()
        assertEquals("Twitter Title", TitleResolver.extractTitle(html, "example.com"))
    }

    @Test
    fun `extractTitle falls back to title tag`() {
        val html = "<html><head><title>Tag Title</title></head></html>"
        assertEquals("Tag Title", TitleResolver.extractTitle(html, "example.com"))
    }

    @Test
    fun `extractTitle handles attributes in different order`() {
        val html = """
            <head>
                <meta content="OG Title" property="og:title" />
            </head>
        """.trimIndent()
        assertEquals("OG Title", TitleResolver.extractTitle(html, "example.com"))
    }

    @Test
    fun `extractTitle rejects generic Twitch title`() {
        val html = """
            <head>
                <meta property="og:title" content="Twitch" />
            </head>
        """.trimIndent()
        assertNull(TitleResolver.extractTitle(html, "twitch.tv"))
    }

    @Test
    fun `extractTitle rejects generic YouTube title`() {
        val html = """
            <head>
                <title>YouTube</title>
            </head>
        """.trimIndent()
        assertNull(TitleResolver.extractTitle(html, "example.com"))
    }

    @Test
    fun `extractTitle returns null for mobile Twitch host`() {
        val html = """
            <head>
                <meta property="og:title" content="Real Stream Title" />
            </head>
        """.trimIndent()
        assertNull(TitleResolver.extractTitle(html, "m.twitch.tv"))
    }

    @Test
    fun `isGenericTitle detects Twitch`() {
        assertTrue(TitleResolver.isGenericTitle("Twitch"))
        assertTrue(TitleResolver.isGenericTitle("twitch"))
    }

    @Test
    fun `isGenericTitle detects YouTube`() {
        assertTrue(TitleResolver.isGenericTitle("YouTube"))
        assertTrue(TitleResolver.isGenericTitle("youtube"))
    }

    @Test
    fun `isGenericTitle accepts real titles`() {
        assertFalse(TitleResolver.isGenericTitle("My Twitch Stream"))
        assertFalse(TitleResolver.isGenericTitle("YouTube Music Mix"))
    }

    @Test
    fun `decodeHtmlEntities decodes common entities`() {
        val encoded = "Tom &amp; Jerry &quot;Hello&quot; &#39;Hi&#39; &lt;tag&gt;"
        assertEquals(
            "Tom & Jerry \"Hello\" 'Hi' <tag>",
            TitleResolver.decodeHtmlEntities(encoded)
        )
    }

}
