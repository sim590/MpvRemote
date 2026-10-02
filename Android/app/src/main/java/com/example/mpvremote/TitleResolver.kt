package com.example.mpvremote

import android.os.Handler
import android.os.Looper
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URLEncoder
import java.net.URL
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors

class TitleResolver {

    private val cache = ConcurrentHashMap<String, String>()
    private val inFlight = ConcurrentHashMap.newKeySet<String>()
    private val executor = Executors.newFixedThreadPool(2)
    private val mainHandler = Handler(Looper.getMainLooper())

    fun resolve(url: String, onResolved: (String) -> Unit) {
        if (url.isBlank()) return

        val cached = cache[url]
        if (cached != null) {
            deliver(onResolved, cached)
            return
        }

        if (!inFlight.add(url)) return

        executor.execute {
            try {
                val title = resolveInternal(url)
                if (title != null) {
                    cache[url] = title
                    deliver(onResolved, title)
                }
            } finally {
                inFlight.remove(url)
            }
        }
    }

    private fun deliver(callback: (String) -> Unit, title: String) {
        mainHandler.post { callback(title) }
    }

    private fun resolveInternal(url: String): String? {
        return if (isYouTubeUrl(url)) {
            resolveYouTube(url)
        } else {
            resolveFromHtml(url)
        }
    }

    private fun resolveYouTube(url: String): String? {
        val oembedUrl = buildYouTubeOEmbedUrl(url) ?: return null
        return try {
            val connection = URL(oembedUrl).openConnection() as HttpURLConnection
            connection.setRequestProperty("User-Agent", DESKTOP_USER_AGENT)
            connection.connectTimeout = 5000
            connection.readTimeout = 5000
            connection.instanceFollowRedirects = true

            val responseCode = connection.responseCode
            if (responseCode !in 200..299) return null
            val body = connection.inputStream.bufferedReader().use { it.readText() }
            JSONObject(body).optString("title").takeIf { it.isNotBlank() }
        } catch (e: Exception) {
            null
        }
    }

    private fun resolveFromHtml(url: String): String? {
        return try {
            val connection = URL(url).openConnection() as HttpURLConnection
            connection.setRequestProperty("User-Agent", DESKTOP_USER_AGENT)
            connection.connectTimeout = 8000
            connection.readTimeout = 8000
            connection.instanceFollowRedirects = true

            val responseCode = connection.responseCode
            if (responseCode !in 200..299) return null
            val finalHost = connection.url.host
            val html = readHead(connection)
            extractTitle(html, finalHost)?.let { decodeHtmlEntities(it) }?.takeIf { it.isNotBlank() }
        } catch (e: Exception) {
            null
        }
    }

    private fun readHead(connection: HttpURLConnection): String {
        val buffer = ByteArray(8192)
        val builder = StringBuilder()
        val maxSize = 256 * 1024
        var total = 0

        connection.inputStream.use { stream ->
            var read: Int
            while (stream.read(buffer).also { read = it } != -1 && total < maxSize) {
                builder.append(String(buffer, 0, read))
                total += read
                val lower = builder.toString().lowercase()
                val idx = lower.indexOf("</head>")
                if (idx >= 0) {
                    builder.setLength(idx + 7)
                    break
                }
            }
        }

        return builder.toString()
    }

    companion object {
        const val DESKTOP_USER_AGENT =
            "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"

        fun isYouTubeUrl(url: String): Boolean {
            val lower = url.lowercase()
            return lower.contains("youtube.com") || lower.contains("youtu.be")
        }

        fun buildYouTubeOEmbedUrl(videoUrl: String): String? {
            val trimmed = videoUrl.trim()
            if (trimmed.isBlank()) return null
            return "https://www.youtube.com/oembed?format=json&url=" +
                    URLEncoder.encode(trimmed, "UTF-8")
        }

        fun extractTitle(html: String, finalHost: String): String? {
            if (finalHost.lowercase().startsWith("m.twitch.tv")) return null

            val ogTitle = extractMetaTag(html, "og:title")
            if (ogTitle != null && !isGenericTitle(ogTitle)) return ogTitle

            val twitterTitle = extractMetaTag(html, "twitter:title")
            if (twitterTitle != null && !isGenericTitle(twitterTitle)) return twitterTitle

            val titleTag = extractTitleTag(html)
            if (titleTag != null && !isGenericTitle(titleTag)) return titleTag

            return null
        }

        fun extractMetaTag(html: String, propertyName: String): String? {
            val tagRegex = Regex(
                "<meta\\s+[^>]*?(?:property|name)\\s*=\\s*[\"']$propertyName[\"'][^>]*?>",
                RegexOption.IGNORE_CASE
            )
            val match = tagRegex.find(html) ?: return null
            val contentRegex = Regex(
                "content\\s*=\\s*[\"']([^\"']*)[\"']",
                RegexOption.IGNORE_CASE
            )
            val contentMatch = contentRegex.find(match.value) ?: return null
            return contentMatch.groupValues[1].trim().takeIf { it.isNotBlank() }
        }

        fun extractTitleTag(html: String): String? {
            val pattern = Regex("<title[^>]*>(.*?)</title>", RegexOption.IGNORE_CASE)
            val match = pattern.find(html) ?: return null
            return match.groupValues[1].trim().takeIf { it.isNotBlank() }
        }

        fun isGenericTitle(title: String): Boolean {
            val lowerTitle = title.lowercase().trim()
            if (lowerTitle == "twitch") return true
            if (lowerTitle == "youtube") return true
            return false
        }

        fun decodeHtmlEntities(text: String): String {
            return text.replace("&amp;", "&")
                .replace("&quot;", "\"")
                .replace("&#39;", "'")
                .replace("&apos;", "'")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
        }
    }
}
