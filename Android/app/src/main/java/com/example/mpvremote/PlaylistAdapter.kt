package com.example.mpvremote

import android.util.TypedValue
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView

data class PlaylistItem(
    val index: Int,
    val title: String,
    val filename: String,
    val current: Boolean,
    val playing: Boolean
)

class PlaylistAdapter(
    private val titleResolver: TitleResolver,
    private val onItemClick: (PlaylistItem) -> Unit,
    private val onRemove: (PlaylistItem) -> Unit
) : RecyclerView.Adapter<PlaylistAdapter.ViewHolder>() {

    private var items: List<PlaylistItem> = emptyList()
    private val resolvedTitles = mutableMapOf<String, String>()

    fun submitList(newItems: List<PlaylistItem>) {
        items = newItems
        notifyDataSetChanged()
        resolveMissingTitles()
    }

    private fun resolveMissingTitles() {
        for (item in items) {
            if (item.title.isBlank()) {
                titleResolver.resolve(item.filename) { resolved ->
                    updateResolvedTitle(item.filename, resolved)
                }
            }
        }
    }

    private fun updateResolvedTitle(filename: String, resolved: String) {
        val index = items.indexOfFirst { it.filename == filename }
        if (index >= 0 && items[index].title.isBlank()) {
            resolvedTitles[filename] = resolved
            notifyItemChanged(index)
        }
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_playlist, parent, false)
        return ViewHolder(view)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        holder.bind(items[position])
    }

    override fun getItemCount(): Int = items.size

    inner class ViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val root: LinearLayout = itemView.findViewById(R.id.playlist_item_root)
        private val titleView: TextView = itemView.findViewById(R.id.playlist_item_title)
        private val subtitleView: TextView = itemView.findViewById(R.id.playlist_item_subtitle)
        private val removeButton: ImageButton = itemView.findViewById(R.id.playlist_item_remove)

        fun bind(item: PlaylistItem) {
            itemView.setOnClickListener { onItemClick(item) }
            removeButton.setOnClickListener { onRemove(item) }

            val resolvedTitle = resolvedTitles[item.filename]
            val displayTitle = item.title.takeIf { it.isNotBlank() }
                ?: resolvedTitle?.takeIf { it.isNotBlank() }
                ?: item.filename

            titleView.text = displayTitle
            if (displayTitle == item.filename && item.title.isBlank()) {
                subtitleView.visibility = View.GONE
            } else {
                subtitleView.text = item.filename
                subtitleView.visibility = View.VISIBLE
            }

            if (item.current) {
                root.setBackgroundColor(itemView.context.getColor(R.color.playlist_item_current))
            } else {
                val outValue = TypedValue()
                itemView.context.theme.resolveAttribute(
                    android.R.attr.selectableItemBackground,
                    outValue,
                    true
                )
                root.setBackgroundResource(outValue.resourceId)
            }
        }
    }
}
