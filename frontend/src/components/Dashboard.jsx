import React, { useState, useEffect, useRef, useMemo } from 'react';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { 
  SquarePen, 
  MessageSquare, 
  LogOut, 
  Download, 
  ArrowUp,
  User,
  ExternalLink,
  FileText,
  Save,
  X,
  Undo,
  Redo,
  Bold,
  Italic,
  Underline,
  Strikethrough,
  List,
  ListOrdered,
  Type,
  AlignLeft,
  AlignCenter,
  AlignRight,
  AlignJustify,
  Highlighter,
  Palette,
  Quote,
  Code,
  Link as LinkIcon,
  Image as ImageIcon
} from 'lucide-react';
import { useEditor, EditorContent } from '@tiptap/react';
import { StarterKit } from '@tiptap/starter-kit';
import { Underline as UnderlineExt } from '@tiptap/extension-underline';
import { TextAlign } from '@tiptap/extension-text-align';
import { Color } from '@tiptap/extension-color';
import { TextStyle } from '@tiptap/extension-text-style';
import { Highlight } from '@tiptap/extension-highlight';
import { Link } from '@tiptap/extension-link';
import { Image } from '@tiptap/extension-image';
import { FontFamily } from '@tiptap/extension-font-family';
import showdown from 'showdown';
import TurndownService from 'turndown';

const converter = new showdown.Converter();
const turndownService = new TurndownService();

const COLORS = [
  '#000000', '#434343', '#666666', '#999999', '#b7b7b7', '#cccccc', '#d9d9d9', '#efefef', '#f3f3f3', '#ffffff',
  '#980000', '#ff0000', '#ff9900', '#ffff00', '#00ff00', '#00ffff', '#4a86e8', '#0000ff', '#9900ff', '#ff00ff',
  '#e6b8af', '#f4cccc', '#fce5cd', '#fff2cc', '#d9ead3', '#d0e0e3', '#c9daf8', '#cfe2f3', '#d9d2e9', '#ead1dc',
  '#dd7e6b', '#ea9999', '#f9cb9c', '#ffe599', '#b6d7a8', '#a2c4c9', '#a4c2f4', '#9fc5e8', '#b4a7d6', '#d5a6bd',
];

const FONTS = [
  { label: 'Default', value: 'Inter, sans-serif' },
  { label: 'Arial', value: 'Arial, Helvetica, sans-serif' },
  { label: 'Arial Black', value: '"Arial Black", Gadget, sans-serif' },
  { label: 'Brush Script', value: '"Brush Script MT", cursive' },
  { label: 'Comic Sans', value: '"Comic Sans MS", cursive' },
  { label: 'Courier New', value: '"Courier New", Courier, monospace' },
  { label: 'Georgia', value: 'Georgia, serif' },
  { label: 'Helvetica', value: 'Helvetica, Arial, sans-serif' },
  { label: 'Impact', value: 'Impact, Charcoal, sans-serif' },
  { label: 'Lucida Console', value: '"Lucida Console", Monaco, monospace' },
  { label: 'Lucida Sans', value: '"Lucida Sans Unicode", "Lucida Grande", sans-serif' },
  { label: 'Palatino', value: '"Palatino Linotype", "Book Antiqua", Palatino, serif' },
  { label: 'Tahoma', value: 'Tahoma, Geneva, sans-serif' },
  { label: 'Times New Roman', value: '"Times New Roman", Times, serif' },
  { label: 'Trebuchet MS', value: '"Trebuchet MS", Helvetica, sans-serif' },
  { label: 'Verdana', value: 'Verdana, Geneva, sans-serif' },
  { label: 'Monospace', value: 'monospace' },
  { label: 'Serif', value: 'serif' },
  { label: 'Cursive', value: 'cursive' },
];

const SIZES = ['8px', '9px', '10px', '11px', '12px', '14px', '16px', '18px', '20px', '24px', '26px', '28px', '36px', '48px', '72px'];

const ColorPickerPopover = ({ onSelect, onClose, current }) => {
  return (
    <div className="color-picker-popover">
      <div className="color-grid">
        {COLORS.map(color => (
          <button
            key={color}
            className={`color-cell ${current === color ? 'active' : ''}`}
            style={{ backgroundColor: color }}
            onClick={() => { onSelect(color); onClose(); }}
            title={color}
          />
        ))}
      </div>
      <button className="clear-color-btn" onClick={() => { onSelect(''); onClose(); }}>
        Clear Color
      </button>
    </div>
  );
};

const MenuBar = ({ editor, filename }) => {
  const [showTextColor, setShowTextColor] = useState(false);
  const [showHighlight, setShowHighlight] = useState(false);
  const fileInputRef = useRef(null);
  
  if (!editor) return null;

  const handleImageUpload = async (event) => {
    const file = event.target.files?.[0];
    if (file && filename) {
      const formData = new FormData();
      formData.append('image', file);
      
      try {
        const token = localStorage.getItem('token');
        const res = await axios.post(`${API_BASE}/blog/${filename}/upload-image`, formData, {
          headers: { 
            'Content-Type': 'multipart/form-data',
            Authorization: `Bearer ${token}` 
          }
        });
        
        if (res.data.url) {
          // res.data.url is like "/images/..."
          const fullUrl = `${API_BASE}${res.data.url}`;
          editor.chain().focus().setImage({ src: fullUrl }).run();
        }
      } catch (err) {
        console.error('Image upload failed', err);
        alert('Failed to upload image. Please try again.');
      }
    }
  };

  return (
    <div className="tiptap-menu-bar">
      <div className="menu-group">
        <select 
          onChange={(e) => editor.chain().focus().setFontFamily(e.target.value).run()}
          className="font-select"
          style={{ minWidth: '120px' }}
          value={editor.getAttributes('textStyle').fontFamily || ''}
        >
          {FONTS.map(font => (
            <option key={font.value} value={font.value}>{font.label}</option>
          ))}
        </select>
        <select 
          onChange={(e) => editor.chain().focus().setFontSize(e.target.value).run()}
          className="font-select"
          style={{ minWidth: '60px' }}
          value={editor.getAttributes('textStyle').fontSize || '16px'}
        >
          {SIZES.map(size => (
            <option key={size} value={size}>{size.replace('px', '')}</option>
          ))}
        </select>
      </div>

      <div className="menu-divider" />

      <div className="menu-group">
        <button onClick={() => editor.chain().focus().undo().run()} disabled={!editor.can().undo()} className="menu-btn" title="Undo"><Undo size={18} /></button>
        <button onClick={() => editor.chain().focus().redo().run()} disabled={!editor.can().redo()} className="menu-btn" title="Redo"><Redo size={18} /></button>
      </div>
      
      <div className="menu-divider" />
      
      <div className="menu-group">
        <button onClick={() => editor.chain().focus().toggleBold().run()} className={`menu-btn ${editor.isActive('bold') ? 'active' : ''}`} title="Bold"><Bold size={18} /></button>
        <button onClick={() => editor.chain().focus().toggleItalic().run()} className={`menu-btn ${editor.isActive('italic') ? 'active' : ''}`} title="Italic"><Italic size={18} /></button>
        <button onClick={() => editor.chain().focus().toggleUnderline().run()} className={`menu-btn ${editor.isActive('underline') ? 'active' : ''}`} title="Underline"><Underline size={18} /></button>
        <button onClick={() => editor.chain().focus().toggleStrike().run()} className={`menu-btn ${editor.isActive('strike') ? 'active' : ''}`} title="Strike"><Strikethrough size={18} /></button>
      </div>

      <div className="menu-divider" />

      <div className="menu-group">
        <button onClick={() => editor.chain().focus().toggleHeading({ level: 1 }).run()} className={`menu-btn ${editor.isActive('heading', { level: 1 }) ? 'active' : ''}`} title="H1">H1</button>
        <button onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()} className={`menu-btn ${editor.isActive('heading', { level: 2 }) ? 'active' : ''}`} title="H2">H2</button>
        <button onClick={() => editor.chain().focus().toggleBulletList().run()} className={`menu-btn ${editor.isActive('bulletList') ? 'active' : ''}`} title="Bullet List"><List size={18} /></button>
        <button onClick={() => editor.chain().focus().toggleOrderedList().run()} className={`menu-btn ${editor.isActive('orderedList') ? 'active' : ''}`} title="Ordered List"><ListOrdered size={18} /></button>
      </div>

      <div className="menu-divider" />

      <div className="menu-group">
        <button onClick={() => editor.chain().focus().setTextAlign('left').run()} className={`menu-btn ${editor.isActive({ textAlign: 'left' }) ? 'active' : ''}`} title="Align Left"><AlignLeft size={18} /></button>
        <button onClick={() => editor.chain().focus().setTextAlign('center').run()} className={`menu-btn ${editor.isActive({ textAlign: 'center' }) ? 'active' : ''}`} title="Align Center"><AlignCenter size={18} /></button>
        <button onClick={() => editor.chain().focus().setTextAlign('right').run()} className={`menu-btn ${editor.isActive({ textAlign: 'right' }) ? 'active' : ''}`} title="Align Right"><AlignRight size={18} /></button>
      </div>

      <div className="menu-divider" />

      <div className="menu-group">
        <div style={{ position: 'relative' }}>
          <button 
            onClick={() => { setShowHighlight(!showHighlight); setShowTextColor(false); }} 
            className={`menu-btn ${editor.isActive('highlight') ? 'active' : ''}`} 
            title="Highlight Color"
          >
            <Highlighter size={18} />
          </button>
          {showHighlight && (
            <ColorPickerPopover 
              current={editor.getAttributes('highlight').color}
              onSelect={(color) => editor.chain().focus().setHighlight({ color }).run()}
              onClose={() => setShowHighlight(false)}
            />
          )}
        </div>

        <div style={{ position: 'relative' }}>
          <button 
            onClick={() => { setShowTextColor(!showTextColor); setShowHighlight(false); }} 
            className="menu-btn" 
            title="Text Color"
          >
            <Palette size={18} />
          </button>
          {showTextColor && (
            <ColorPickerPopover 
              current={editor.getAttributes('textStyle').color}
              onSelect={(color) => editor.chain().focus().setColor(color).run()}
              onClose={() => setShowTextColor(false)}
            />
          )}
        </div>
        
        <button onClick={() => editor.chain().focus().toggleBlockquote().run()} className={`menu-btn ${editor.isActive('blockquote') ? 'active' : ''}`} title="Quote"><Quote size={18} /></button>
        <button onClick={() => editor.chain().focus().toggleCodeBlock().run()} className={`menu-btn ${editor.isActive('codeBlock') ? 'active' : ''}`} title="Code Block"><Code size={18} /></button>
        <button onClick={() => fileInputRef.current.click()} className="menu-btn" title="Upload Image">
          <ImageIcon size={18} />
          <input 
            type="file" 
            ref={fileInputRef} 
            onChange={handleImageUpload} 
            accept="image/*" 
            style={{ display: 'none' }}
          />
        </button>
      </div>
    </div>
  );
};

const API_BASE = 'http://localhost:8000';

const CustomTextStyle = TextStyle.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      fontWeight: {
        default: null,
        parseHTML: element => element.style.fontWeight,
        renderHTML: attributes => {
          if (!attributes.fontWeight) return {};
          return { style: `font-weight: ${attributes.fontWeight}` };
        },
      },
      fontSize: {
        default: null,
        parseHTML: element => element.style.fontSize,
        renderHTML: attributes => {
          if (!attributes.fontSize) return {};
          return { style: `font-size: ${attributes.fontSize}` };
        },
      },
    }
  },
  addCommands() {
    return {
      setFontWeight: fontWeight => ({ chain }) => {
        return chain().setMark('textStyle', { fontWeight }).run();
      },
      setFontSize: fontSize => ({ chain }) => {
        return chain().setMark('textStyle', { fontSize }).run();
      },
    };
  },
})

const Dashboard = () => {
  const [pastBlogs, setPastBlogs] = useState([]);
  const [selectedBlog, setSelectedBlog] = useState(null);
  const [topic, setTopic] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);
  const [generatedBlog, setGeneratedBlog] = useState(null);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState(0); // 0 to 4 steps
  const [user, setUser] = useState(null);
  const [isEditing, setIsEditing] = useState(false);
  const [editedContent, setEditedContent] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const textareaRef = useRef(null);
  
  const editor = useEditor({
    extensions: [
      StarterKit,
      UnderlineExt,
      TextAlign.configure({ types: ['heading', 'paragraph'] }),
      CustomTextStyle,
      Color,
      Highlight.configure({ multicolor: true }),
      Link.configure({ openOnClick: false }),
      Image,
      FontFamily,
    ],
    content: '',
    onUpdate: ({ editor }) => {
      setEditedContent(editor.getHTML());
    },
  });

  useEffect(() => {
    fetchPastBlogs();
    fetchUserProfile();
  }, []);

  const fetchUserProfile = async () => {
    const token = localStorage.getItem('token');
    if (!token) return;
    try {
      const res = await axios.get(`${API_BASE}/me`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      setUser(res.data);
    } catch (err) {
      console.error('Failed to fetch user profile', err);
    }
  };

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [topic]);

  const fetchPastBlogs = async () => {
    const token = localStorage.getItem('token');
    if (!token) return;
    try {
      const res = await axios.get(`${API_BASE}/past-blogs`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      setPastBlogs(res.data);
    } catch (err) {
      console.error('Failed to fetch past blogs', err);
    }
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
    window.location.reload();
  };

  const startNewChat = () => {
    setSelectedBlog(null);
    setGeneratedBlog(null);
    setTopic('');
    setError('');
    setProgress(0);
  };

  const handleBlogSelect = async (blog) => {
    const token = localStorage.getItem('token');
    try {
      const res = await axios.get(`${API_BASE}/blog/${blog.filename}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      setSelectedBlog({ ...blog, content: res.data.content });
      setGeneratedBlog(null);
      setError('');
    } catch (err) {
      console.error('Failed to fetch blog content', err);
    }
  };

  const handleGenerate = async (e) => {
    if (e) e.preventDefault();
    if (!topic.trim() || isGenerating) return;

    setIsGenerating(true);
    setGeneratedBlog(null);
    setSelectedBlog(null);
    setError('');
    
    // Simulate progress dots
    let p = 0;
    const interval = setInterval(() => {
      p = (p + 1) % 5;
      setProgress(p);
    }, 2000);

    try {
      const token = localStorage.getItem('token');
      const res = await axios.post(`${API_BASE}/generate-blog`, { topic }, {
        headers: { Authorization: `Bearer ${token}` }
      });
      setGeneratedBlog(res.data);
      fetchPastBlogs();
    } catch (err) {
      setError('Generation failed. Please try again.');
      console.error(err);
    } finally {
      clearInterval(interval);
      setIsGenerating(false);
      setProgress(4);
      setIsEditing(false); // Reset editing mode on new generation
    }
  };

  const handleEdit = () => {
    const rawContent = generatedBlog?.final || selectedBlog?.content || '';
    const htmlContent = converter.makeHtml(rawContent);
    setEditedContent(htmlContent);
    if (editor) {
      editor.commands.setContent(htmlContent);
    }
    setIsEditing(true);
  };

  const handleCancelEdit = () => {
    setIsEditing(false);
  };

  const handleSave = async () => {
    const filename = generatedBlog?.filename || selectedBlog?.filename;
    if (!filename) return;

    setIsSaving(true);
    try {
      // Convert HTML back to Markdown
      const markdownContent = turndownService.turndown(editedContent);
      
      const token = localStorage.getItem('token');
      await axios.put(`${API_BASE}/blog/${filename}`, 
        { content: markdownContent },
        { headers: { Authorization: `Bearer ${token}` } }
      );
      
      // Update local state
      if (generatedBlog) {
        setGeneratedBlog({ ...generatedBlog, final: markdownContent });
      } else if (selectedBlog) {
        setSelectedBlog({ ...selectedBlog, content: markdownContent });
      }
      
      setIsEditing(false);
      setError('');
    } catch (err) {
      console.error('Failed to save blog', err);
      setError('Failed to save changes.');
    } finally {
      setIsSaving(false);
    }
  };

  const handleDownload = async (filename, type) => {
    if (!filename) {
      setError('Filename not available for download.');
      return;
    }
    const url = type === 'md' ? `${API_BASE}/download-md/${filename}` : `${API_BASE}/download-docx/${filename}`;
    try {
      const token = localStorage.getItem('token');
      const response = await axios.get(url, { 
        responseType: 'blob',
        headers: { Authorization: `Bearer ${token}` }
      });
      
      // Get filename from Content-Disposition header
      const contentDisposition = response.headers['content-disposition'] || response.headers['Content-Disposition'];
      let downloadFilename = filename;
      
      if (contentDisposition) {
        // Try to match filename="filename" or filename=filename
        const filenameMatch = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
        if (filenameMatch && filenameMatch[1]) {
          downloadFilename = filenameMatch[1].replace(/['"]/g, '').trim();
        }
      }

      // Final fallback for extension
      if (type === 'docx' && !downloadFilename.toLowerCase().endsWith('.docx')) {
        downloadFilename = downloadFilename.replace(/\.md$/i, '') + '.docx';
      } else if (type === 'md' && !downloadFilename.toLowerCase().endsWith('.md')) {
        downloadFilename += '.md';
      }
      
      const blob = new Blob([response.data], { type: response.headers['content-type'] });
      const downloadUrl = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = downloadUrl;
      link.download = downloadFilename; // This is the important part
      
      document.body.appendChild(link);
      link.click();
      
      // Cleanup
      setTimeout(() => {
        document.body.removeChild(link);
        window.URL.revokeObjectURL(downloadUrl);
      }, 100);
      
      setError('');
    } catch (err) {
      console.error('Download failed', err);
      setError('Download failed. Please check the console for details.');
    }
  };

  const renderBlog = (content) => {
    return (
      <div className="blog-output">
        <ReactMarkdown 
          remarkPlugins={[remarkGfm]}
          components={{
            img: ({ node, ...props }) => {
              const src = props.src.startsWith('http') ? props.src : `${API_BASE}/${props.src}`;
              return <img {...props} src={src} alt={props.alt || 'blog image'} />;
            }
          }}
        >
          {content}
        </ReactMarkdown>
      </div>
    );
  };

  return (
    <div className="dashboard-layout">
      {/* Sidebar */}
      <div className="sidebar">
        <button className="new-chat-btn" onClick={startNewChat}>
          <SquarePen size={20} />
          <span>New Blog</span>
        </button>
        
        <div style={{ flex: 1, overflowY: 'auto' }}>
          <div style={{ fontSize: '0.75rem', fontWeight: 600, color: 'rgba(255,255,255,0.5)', margin: '1rem 0 0.5rem 0.8rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>History</div>
          {pastBlogs.length > 0 ? (
            pastBlogs.map((blog) => (
              <div 
                key={blog.filename} 
                className={`past-blog-item ${selectedBlog?.filename === blog.filename ? 'active' : ''}`}
                onClick={() => handleBlogSelect(blog)}
                title={blog.title}
              >
                {blog.title}
              </div>
            ))
          ) : (
            <div style={{ padding: '0.8rem', fontSize: '0.85rem', color: 'rgba(255,255,255,0.6)', fontStyle: 'italic' }}>
              No blogs found
            </div>
          )}
        </div>

        <div className="user-profile" style={{ cursor: 'default', borderBottom: '1px solid rgba(255,255,255,0.05)', marginBottom: '0.5rem', paddingBottom: '0.5rem' }}>
          <div className="user-avatar" style={{ background: '#4338ca' }}>
            {user?.email?.[0]?.toUpperCase() || <User size={16} />}
          </div>
          <div style={{ flex: 1, fontSize: '0.85rem', color: '#ececec', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {user?.email}
          </div>
        </div>

        <div className="user-profile" onClick={handleLogout} style={{ marginTop: 0 }}>
          <div style={{ width: 32, display: 'flex', justifyContent: 'center' }}>
            <LogOut size={16} />
          </div>
          <div style={{ flex: 1, fontSize: '0.9rem' }}>Log Out</div>
        </div>
      </div>

      {/* Main Content */}
      <div className="main-content">
        {!selectedBlog && !generatedBlog && !isGenerating ? (
          <div className="welcome-section">
            <h1 className="welcome-title">Ready when you are.</h1>
          </div>
        ) : (
          <div className="chat-container">
            {isGenerating ? (
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginTop: '4rem' }}>
                <div className="status-indicator" style={{ position: 'static', marginBottom: '1rem' }}>
                  {[1, 2, 3, 4].map(i => (
                    <div key={i} className={`progress-dot ${progress >= i ? 'active' : ''}`} />
                  ))}
                </div>
                <div style={{ color: '#666', fontSize: '0.9rem' }}>Agents are researching and writing...</div>
              </div>
            ) : (
              <>
                <div className="download-actions-container">
                  {!isEditing ? (
                    <>
                      <button 
                        onClick={handleEdit}
                        className="download-btn edit-btn"
                      >
                        <SquarePen size={16} />
                        Edit Blog
                      </button>
                      <button 
                        onClick={() => handleDownload(generatedBlog?.filename || selectedBlog?.filename, 'md')}
                        className="download-btn"
                      >
                        <FileText size={16} />
                        Download MD
                      </button>
                      <button 
                        onClick={() => handleDownload(generatedBlog?.filename || selectedBlog?.filename, 'docx')}
                        className="download-btn"
                      >
                        <Download size={16} />
                        Download DOCX
                      </button>
                    </>
                  ) : (
                    <>
                      <button 
                        onClick={handleSave}
                        className="download-btn save-btn"
                        disabled={isSaving}
                      >
                        <Save size={16} />
                        {isSaving ? 'Saving...' : 'Save Changes'}
                      </button>
                      <button 
                        onClick={handleCancelEdit}
                        className="download-btn cancel-btn"
                        disabled={isSaving}
                      >
                        <X size={16} />
                        Cancel
                      </button>
                    </>
                  )}
                </div>
                {isEditing ? (
                   <div className="editor-container tiptap-container" style={{ position: 'relative', zIndex: 10 }}>
                    <MenuBar editor={editor} filename={generatedBlog?.filename || selectedBlog?.filename} />
                    <EditorContent editor={editor} className="tiptap-editor" />
                  </div>
                ) : (
                  renderBlog(generatedBlog?.final || selectedBlog?.content)
                )}
              </>
            )}
          </div>
        )}

        {/* Input Bar */}
        {!selectedBlog && !generatedBlog && !isGenerating && (


          <>
            <div className="input-container">
              <div className="prompt-bar-wrapper">
                <textarea
                  ref={textareaRef}
                  className="prompt-input"
                  rows="1"
                  placeholder="Topic for your next blog..."
                  value={topic}
                  onChange={(e) => setTopic(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      handleGenerate();
                    }
                  }}
                  disabled={isGenerating}
                />
                <button 
                  className="gen-btn" 
                  disabled={isGenerating || !topic.trim()}
                  onClick={handleGenerate}
                >
                  <ArrowUp size={18} />
                </button>
              </div>
            </div>
            
            <div style={{ fontSize: '0.7rem', color: '#999', textAlign: 'center', paddingBottom: '0.5rem' }}>
              Blog Writing Agent can make mistakes. Check important info.
            </div>
          </>
        )}
      </div>
    </div>
  );
};


export default Dashboard;
